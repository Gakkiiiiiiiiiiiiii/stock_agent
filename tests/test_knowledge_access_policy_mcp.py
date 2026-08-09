"""P0-10 / P0-11 / P0-12 / §85 回归测试：MCP 质量门、枚举统一、API 新端点。"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from app.api import app
from engines.content.knowledge_enums import (
    KnowledgeKind,
    LifecycleStatus,
    TemporalClass,
    VerificationStatus,
)
from engines.content.video_ingest_service import VideoIngestService
from storage.db import Base, SessionLocal
from storage.models.content import VideoAsset
from storage.models.knowledge import KnowledgeUnit, VideoChapter
from storage.repositories.knowledge_repository import KnowledgeRepository


def configure_test_db(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'knowledge_access_policy.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def fresh_tmp_path(prefix: str) -> Path:
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=temp_root))


def seed_unit(
    *,
    uid: str,
    statement: str,
    support_status: str,
    support_probability: float | None = None,
    truth_status: str = "NOT_CHECKED",
    review_status: str = "UNREVIEWED",
) -> int:
    with SessionLocal() as session:
        video = VideoAsset(platform="bilibili", platform_video_id=f"BVAP{uid}", bvid=f"BVAP{uid}", url=f"https://example.com/{uid}", title="质量门测试")
        session.add(video)
        session.flush()
        chapter = VideoChapter(
            video_id=video.id,
            chapter_index=0,
            parent_chapter_id=None,
            start_ms=0,
            end_ms=1000,
            title="章节",
            chapter_type="ANALYSIS",
            primary_domain="MARKET",
            content_hash=f"chapter-hash-{uid}",
            parser_version="test",
        )
        session.add(chapter)
        session.flush()
        unit = KnowledgeUnit(
            knowledge_uid=f"ku-{uid}",
            source_video_id=video.id,
            source_chapter_id=chapter.id,
            primary_domain="MARKET",
            knowledge_kind="FACT",
            temporal_class="SNAPSHOT",
            expression_type="AUTHOR_EXPLICIT",
            subject_type="THEME",
            subject_key="黄金",
            subject_name="黄金",
            predicate_key="走势",
            statement=statement,
            canonical_statement=statement,
            as_of_time=datetime.now(UTC),
            lifecycle_status="ACTIVE",
            verification_status="UNVERIFIED",
            support_status=support_status,
            support_probability=support_probability,
            truth_status=truth_status,
            review_status=review_status,
            content_hash=f"unit-hash-{uid}",
            extractor_version="test",
        )
        session.add(unit)
        session.commit()
        return unit.id


def make_search_service() -> VideoIngestService:
    """只挂 knowledge_repo 的轻量 service 实例（search_video_knowledge 不依赖其他子服务）。"""
    service = VideoIngestService.__new__(VideoIngestService)
    service.knowledge_repo = KnowledgeRepository()
    return service


def test_mcp_enum_matches_api_enum():
    """P0-11 / §36：MCP 与 API 的 VALID_* 集合一致，且与 knowledge_enums 单一事实来源一致。"""
    import app.api as api_module
    import mcp_servers.content_server as mcp_module

    assert mcp_module.VALID_KNOWLEDGE_KINDS == api_module.VALID_KNOWLEDGE_KINDS == KnowledgeKind.values()
    assert mcp_module.VALID_TEMPORAL_CLASSES == api_module.VALID_TEMPORAL_CLASSES == TemporalClass.values()
    assert mcp_module.VALID_LIFECYCLE_STATUSES == api_module.VALID_LIFECYCLE_STATUSES == LifecycleStatus.values()
    assert mcp_module.VALID_VERIFICATION_STATUSES == api_module.VALID_VERIFICATION_STATUSES == VerificationStatus.values()
    # P0-11 修复点：MCP 不再缺新状态。
    assert {"UNSUPPORTED", "SOURCE_LOCATED", "SOURCE_SUPPORTED", "CROSS_MODAL_SUPPORTED", "EXTERNALLY_VERIFIED"} <= mcp_module.VALID_VERIFICATION_STATUSES
    assert {"VALUATION", "FINANCIAL_METRIC", "PRICE_LEVEL", "POLICY_FACT"} <= mcp_module.VALID_KNOWLEDGE_KINDS


def test_mcp_search_applies_research_policy_by_default():
    """P0-10 / §85：不传 support 过滤时，默认 research policy 不会取到 SOURCE_LOCATED / UNSUPPORTED。"""
    configure_test_db(fresh_tmp_path("knowledge-policy-research-"))
    seed_unit(uid="low", statement="黄金低质量观点", support_status="SOURCE_LOCATED", support_probability=0.9)
    seed_unit(uid="unsupported", statement="黄金无证据观点", support_status="UNSUPPORTED", support_probability=0.9)
    seed_unit(uid="high", statement="黄金有证据观点", support_status="SOURCE_SUPPORTED", support_probability=0.8)

    service = make_search_service()
    payload = service.search_video_knowledge("黄金")

    statements = [item["statement"] for item in payload["items"]]
    assert statements == ["黄金有证据观点"]
    # policy filter 已合并进 filters（调用方只能收紧）。
    assert payload["filters"]["minimum_support_status"] == "SOURCE_SUPPORTED"
    assert payload["filters"]["minimum_support_probability"] == pytest.approx(0.6)
    assert "REJECTED" in payload["filters"]["denied_review_status"]

    # 人工 REJECTED 的高质量知识也被 review gate 排除。
    seed_unit(uid="rejected", statement="黄金被驳回观点", support_status="SOURCE_SUPPORTED", support_probability=0.9, review_status="REJECTED")
    statements = [item["statement"] for item in service.search_video_knowledge("黄金")["items"]]
    assert statements == ["黄金有证据观点"]

    # 调用方只能收紧不能放宽：更低的 score 门槛不生效，更高的生效。
    relaxed = service.search_video_knowledge("黄金", filters={"minimum_support_probability": 0.1, "minimum_support_status": "UNSUPPORTED"})
    assert relaxed["filters"]["minimum_support_probability"] == pytest.approx(0.6)
    assert relaxed["filters"]["minimum_support_status"] == "SOURCE_SUPPORTED"
    tightened = service.search_video_knowledge("黄金", filters={"minimum_support_probability": 0.9})
    assert tightened["items"] == []
    assert tightened["filters"]["minimum_support_probability"] == pytest.approx(0.9)


def test_mcp_factual_intent_requires_external_truth():
    """P0-10 / §85：factual_qa intent 要求 EXTERNALLY_VERIFIED truth + 0.7 score + valid_only。"""
    configure_test_db(fresh_tmp_path("knowledge-policy-factual-"))
    seed_unit(uid="unchecked", statement="黄金未外验事实", support_status="SOURCE_SUPPORTED", support_probability=0.9, truth_status="NOT_CHECKED")
    seed_unit(uid="verified", statement="黄金已外验事实", support_status="SOURCE_SUPPORTED", support_probability=0.9, truth_status="EXTERNALLY_VERIFIED")

    service = make_search_service()
    payload = service.search_video_knowledge("黄金", intent="factual_qa")

    assert [item["statement"] for item in payload["items"]] == ["黄金已外验事实"]
    assert payload["filters"]["truth_status"] == "EXTERNALLY_VERIFIED"
    assert payload["filters"]["minimum_support_probability"] == pytest.approx(0.7)
    assert payload["filters"]["valid_only"] is True


def test_mcp_tool_passes_intent_to_service(monkeypatch):
    """MCP 层不做 merge，只透传 intent + 原始 filters（merge 只在 service 层一次）。"""
    import mcp_servers.content_server as mcp_module

    captured = {}

    class FakeService:
        def search_video_knowledge(self, query, filters=None, limit=5, intent=None):
            captured["query"] = query
            captured["filters"] = filters
            captured["limit"] = limit
            captured["intent"] = intent
            return {"query": query, "items": [], "limit": limit, "filters": filters or {}, "warnings": []}

    monkeypatch.setattr(mcp_module, "service", FakeService())
    result = mcp_module.search_video_knowledge("黄金", intent="factual_qa", top_k=3)

    assert captured["intent"] == "factual_qa"
    assert "minimum_support_status" not in (captured["filters"] or {})
    assert result["intent"] == "factual_qa"


class FakeApiContentService:
    def get_knowledge_unit(self, unit_id):
        if unit_id != 99:
            return None
        return {
            "id": 99,
            "canonical_statement": "知识",
            "source_reliability_score": 0.8,
            "evidence": [
                {
                    "source_type": "ASR",
                    "evidence_text": "证据文本",
                    "raw_text": "原始文本",
                    "word_timestamps": [{"word": "原始", "start_ms": 0, "end_ms": 100}],
                    "correction_trace": [{"type": "DICTIONARY_CORRECTION"}],
                    "bbox": [],
                }
            ],
        }

    def search_video_knowledge(self, query, filters=None, limit=20, intent=None):
        return {"query": query, "intent": intent, "filters": filters or {}, "items": [], "limit": limit, "next_cursor": None, "warnings": []}

    def update_knowledge_unit_lifecycle(self, unit_id, lifecycle_status=None, verification_status=None, review_status=None, valid_to=None, note=None, operator=None):
        if unit_id != 99:
            return None
        return {
            "id": 99,
            "lifecycle_status": lifecycle_status,
            "verification_status": verification_status,
            "review_status": review_status,
            "operator": operator,
            "vector_tasks": [],
        }


class FakeKnowledgeRepository:
    def list_verifications(self, unit_id, limit=100):
        assert unit_id == 99
        return [
            {
                "id": 1,
                "knowledge_unit_id": 99,
                "verifier_type": "MANUAL_REVIEW",
                "status": "APPROVED",
                "detail": {"operator": "admin"},
                "provenance": {"provider": "manual"},
            }
        ]


client = TestClient(app)


def test_api_search_passes_intent(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeApiContentService())
    response = client.post("/api/v1/content/knowledge/search", json={"query": "黄金", "intent": "factual_qa", "limit": 5})
    assert response.status_code == 200
    assert response.json()["intent"] == "factual_qa"


def test_api_lifecycle_update_passes_review_status(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeApiContentService())
    response = client.patch("/api/v1/content/knowledge/99/lifecycle", json={"review_status": "APPROVED", "operator": "admin", "note": "人工确认"})
    assert response.status_code == 200
    assert response.json()["review_status"] == "APPROVED"
    missing = client.patch("/api/v1/content/knowledge/100/lifecycle", json={"review_status": "APPROVED"})
    assert missing.status_code == 404


def test_api_list_knowledge_verifications(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeApiContentService())
    monkeypatch.setattr("app.api.knowledge_repository", FakeKnowledgeRepository())
    response = client.get("/api/v1/content/knowledge/99/verifications")
    assert response.status_code == 200
    payload = response.json()
    assert payload["knowledge_unit_id"] == 99
    assert payload["items"][0]["verifier_type"] == "MANUAL_REVIEW"
    assert payload["items"][0]["provenance"] == {"provider": "manual"}
    missing = client.get("/api/v1/content/knowledge/100/verifications")
    assert missing.status_code == 404


def test_api_get_knowledge_evidence(monkeypatch):
    monkeypatch.setattr("app.api.content_ingest_service", FakeApiContentService())
    response = client.get("/api/v1/content/knowledge/99/evidence")
    assert response.status_code == 200
    payload = response.json()
    assert payload["knowledge_unit_id"] == 99
    evidence = payload["items"][0]
    assert evidence["raw_text"] == "原始文本"
    assert evidence["word_timestamps"]
    assert evidence["correction_trace"]
    missing = client.get("/api/v1/content/knowledge/100/evidence")
    assert missing.status_code == 404

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, select

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from storage.db import Base, SessionLocal
from storage.models.content import VideoAsset
from storage.models.knowledge import KnowledgeVerification
from storage.repositories.knowledge_repository import KnowledgeRepository, KnowledgeVectorTaskService


def configure_test_db(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'knowledge_axes.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def make_tmp_path(prefix: str) -> Path:
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=temp_root))


def seed_video(suffix: str) -> int:
    with SessionLocal() as session:
        video = VideoAsset(
            platform="bilibili",
            platform_video_id=f"BVAX{suffix}",
            bvid=f"BVAX{suffix}",
            url=f"https://example.com/{suffix}",
            title="三轴状态测试",
        )
        session.add(video)
        session.commit()
        return video.id


def make_unit(uid_suffix: str, **overrides) -> dict:
    unit = {
        "chapter_index": 0,
        "knowledge_uid": f"ku-axes-{uid_suffix}",
        "primary_domain": "MARKET",
        "knowledge_kind": "STATE",
        "temporal_class": "SNAPSHOT",
        "expression_type": "AUTHOR_EXPLICIT",
        "subject_type": "THEME",
        "subject_key": "券商",
        "subject_name": "券商",
        "statement": "券商当前处于活跃状态",
        "canonical_statement": "券商当前处于活跃状态",
        "lifecycle_status": "ACTIVE",
        "support_status": "SOURCE_SUPPORTED",
        "content_hash": f"unit-hash-{uid_suffix}",
        "extractor_version": "test",
        "evidence": [
            {
                "source_type": "ASR",
                "evidence_text": "券商活跃",
                "start_ms": 0,
                "end_ms": 1000,
                "confidence_score": 0.9,
                "is_primary": True,
            }
        ],
    }
    unit.update(overrides)
    return unit


def seed_units(prefix: str, units: list[dict]) -> tuple[KnowledgeRepository, list[int]]:
    tmp_path = make_tmp_path(prefix)
    configure_test_db(tmp_path)
    video_id = seed_video(prefix)
    repo = KnowledgeRepository()
    result = repo.replace_video_knowledge(
        video_id=video_id,
        chapters=[
            {
                "chapter_index": 0,
                "start_ms": 0,
                "end_ms": 1000,
                "title": "章节",
                "chapter_type": "ANALYSIS",
                "primary_domain": "MARKET",
                "content_hash": f"chapter-hash-{prefix}",
                "parser_version": "test",
            }
        ],
        units=units,
    )
    return repo, list(result["knowledge_unit_ids"])


def test_replace_video_knowledge_persists_review_axes():
    repo, unit_ids = seed_units(
        "axes-persist-",
        [
            make_unit("approved", review_status="APPROVED", evidence_quality_status="HIGH", support_score=0.82),
            make_unit("defaults"),
        ],
    )

    approved = repo.get_unit(unit_ids[0])
    defaulted = repo.get_unit(unit_ids[1])

    assert approved["review_status"] == "APPROVED"
    assert approved["evidence_quality_status"] == "HIGH"
    assert approved["support_score"] == 0.82
    assert defaulted["review_status"] == "UNREVIEWED"
    assert defaulted["evidence_quality_status"] == "UNKNOWN"
    assert defaulted["support_score"] is None


def test_verification_ledger_records_all_verifier_types():
    attributes = {
        "verification": {
            "support_status": "SOURCE_SUPPORTED",
            "support_probability": 0.9,
            "checks": {"numeric_binding": True},
            "reason_codes": ["SUPPORT_OK"],
        },
        "external_verification": {
            "status": "MATCH",
            "score": 0.95,
            "provider": "tushare",
            "source_id": "600030.SH",
            "as_of": "2026-08-01",
            "observed_value": 21.5,
        },
        "cross_modal_verification": {"status": "CONSISTENT", "score": 0.7},
        "entity_resolution": {"status": "RESOLVED", "score": 0.88},
    }
    repo, unit_ids = seed_units("axes-ledger-", [make_unit("ledger", attributes=attributes)])

    with SessionLocal() as session:
        rows = list(
            session.execute(
                select(KnowledgeVerification).where(KnowledgeVerification.knowledge_unit_id == unit_ids[0])
            ).scalars()
        )

    by_type = {row.verifier_type: row for row in rows}
    # attributes["verification"] 保留历史 verifier_type；其余按 VerifierType 字面量入账。
    assert set(by_type) == {"claim_evidence_semantic", "EXTERNAL_FACT", "CROSS_MODAL", "ENTITY_RESOLUTION"}

    semantic = by_type["claim_evidence_semantic"]
    assert semantic.status == "SOURCE_SUPPORTED"
    assert semantic.score == 0.9
    assert semantic.verifier_provider == "deterministic"
    assert semantic.verifier_version == "v1"
    assert '"numeric_binding": true' in semantic.checks_json
    assert "SUPPORT_OK" in semantic.reason_codes_json

    external = by_type["EXTERNAL_FACT"]
    assert external.status == "MATCH"
    assert external.score == 0.95
    assert external.verifier_provider == "tushare"
    provenance = external.provenance_json
    assert '"source_id": "600030.SH"' in provenance
    assert '"as_of": "2026-08-01"' in provenance
    assert '"observed_value": 21.5' in provenance

    assert by_type["CROSS_MODAL"].status == "CONSISTENT"
    assert by_type["ENTITY_RESOLUTION"].status == "RESOLVED"

    serialized = repo.list_verifications(unit_ids[0])
    assert len(serialized) == 4
    assert all("evidence_id" in item and "provenance" in item for item in serialized)
    external_payload = next(item for item in serialized if item["verifier_type"] == "EXTERNAL_FACT")
    assert external_payload["provenance"]["provider"] == "tushare"


def test_apply_unit_filters_support_and_review_axes():
    repo, _ = seed_units(
        "axes-filter-",
        [
            make_unit("unsupported", subject_key="标的A", support_status="UNSUPPORTED"),
            make_unit("located", subject_key="标的B", support_status="SOURCE_LOCATED"),
            make_unit("supported", subject_key="标的C", support_status="SOURCE_SUPPORTED", review_status="APPROVED", support_score=0.9),
            make_unit("crossmodal", subject_key="标的D", support_status="CROSS_MODAL_SUPPORTED", support_score=0.4),
            make_unit("external", subject_key="标的E", support_status="EXTERNALLY_VERIFIED", review_status="REJECTED"),
            make_unit("validated", subject_key="标的F", support_status="VALIDATED", support_score=0.7),
        ],
    )

    def uids(filters: dict) -> set[str]:
        return {unit["knowledge_uid"] for unit in repo.search_units("", filters=filters, limit=50)}

    minimum = uids({"minimum_support_status": "SOURCE_SUPPORTED"})
    assert minimum == {"ku-axes-supported", "ku-axes-crossmodal", "ku-axes-external", "ku-axes-validated"}

    assert uids({"review_status": "APPROVED"}) == {"ku-axes-supported"}
    assert uids({"review_status": ["APPROVED", "UNREVIEWED"]}) == {
        "ku-axes-unsupported",
        "ku-axes-located",
        "ku-axes-supported",
        "ku-axes-crossmodal",
        "ku-axes-validated",
    }

    denied = uids({"denied_review_status": "REJECTED"})
    assert "ku-axes-external" not in denied
    assert len(denied) == 5

    # minimum_support_score 排除低于门槛与 support_score 为 NULL 的行。
    assert uids({"minimum_support_score": 0.5}) == {"ku-axes-supported", "ku-axes-validated"}


def test_get_current_subject_state_excludes_review_rejected():
    repo, _ = seed_units(
        "axes-current-",
        [
            make_unit("kept", support_status="SOURCE_SUPPORTED", review_status="APPROVED", support_score=0.6),
            make_unit("rejected", support_status="SOURCE_SUPPORTED", review_status="REJECTED", support_score=0.95),
        ],
    )

    current = repo.get_current_subject_state("券商")
    assert [item["knowledge_uid"] for item in current["items"]] == ["ku-axes-kept"]

    gated = repo.get_current_subject_state("券商", minimum_support_score=0.7)
    assert gated["items"] == []


def test_is_indexable_review_gate():
    base = {"lifecycle_status": "ACTIVE", "support_status": "SOURCE_SUPPORTED"}
    assert KnowledgeVectorTaskService.is_indexable(base) is True
    assert KnowledgeVectorTaskService.is_indexable(base | {"review_status": "APPROVED"}) is True
    assert KnowledgeVectorTaskService.is_indexable(base | {"review_status": "REJECTED"}) is False
    assert KnowledgeVectorTaskService.is_indexable(base | {"review_status": "rejected"}) is False
    assert KnowledgeVectorTaskService.is_indexable({"lifecycle_status": "RETIRED", "support_status": "SOURCE_SUPPORTED"}) is False

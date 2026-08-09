"""P1-5 SourceReliabilityService 测试（§56-57）：factual_accuracy / sample_size / forecast_score / backfill。"""

from __future__ import annotations

from sqlalchemy import create_engine, select

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from engines.content.source_reliability_service import SourceReliabilityService
from storage.db import Base, SessionLocal, session_scope
from storage.models.content import VideoAsset
from storage.models.knowledge import KnowledgeUnit
from storage.repositories.knowledge_repository import KnowledgeRepository


def _configure_db(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reliability.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def _seed_video(suffix: str, author_id: str | None, author_name: str | None = None) -> int:
    with SessionLocal() as session:
        video = VideoAsset(
            platform="bilibili",
            platform_video_id=f"BVREL{suffix}",
            bvid=f"BVREL{suffix}",
            url=f"https://example.com/{suffix}",
            title="可靠性测试",
            author_id=author_id,
            author_name=author_name,
        )
        session.add(video)
        session.commit()
        return video.id


def _unit(uid: str, **overrides) -> dict:
    unit = {
        "chapter_index": 0,
        "knowledge_uid": uid,
        "primary_domain": "MARKET",
        "knowledge_kind": "FACT",
        "temporal_class": "SNAPSHOT",
        "expression_type": "AUTHOR_EXPLICIT",
        "subject_key": "券商",
        "subject_name": "券商",
        "statement": f"陈述{uid}",
        "canonical_statement": f"陈述{uid}",
        "lifecycle_status": "ACTIVE",
        "support_status": "SOURCE_SUPPORTED",
        "content_hash": f"hash-{uid}",
        "extractor_version": "test",
    }
    unit.update(overrides)
    return unit


def _seed_units(repo: KnowledgeRepository, video_id: int, suffix: str, units: list[dict], relations: list[dict] | None = None) -> None:
    repo.replace_video_knowledge(
        video_id=video_id,
        chapters=[{
            "chapter_index": 0,
            "start_ms": 0,
            "end_ms": 1000,
            "title": "章节",
            "chapter_type": "ANALYSIS",
            "primary_domain": "MARKET",
            "content_hash": f"chapter-{suffix}",
            "parser_version": "test",
        }],
        units=units,
        relations=relations,
    )


def _seed_main(tmp_path) -> None:
    """author-1：eligible 2 条（1 verified / 1 conflict）+ 1 条未跑外部验证 + 5 条 FORECAST（2 条被印证）。"""
    _configure_db(tmp_path)
    repo = KnowledgeRepository()
    video_a = _seed_video("a", "author-1", "作者甲")
    _seed_units(repo, video_a, "a", [
        _unit("u1", truth_status="EXTERNALLY_VERIFIED", external_verification_status="EXTERNAL_MATCH"),
        _unit("u2", truth_status="EXTERNAL_CONFLICT", external_verification_status="EXTERNAL_CONFLICT"),
        _unit("u3", truth_status="NOT_CHECKED", external_verification_status="NOT_RUN"),
        _unit("f1", knowledge_kind="FORECAST", lifecycle_status="VALIDATED"),
        _unit("f2", knowledge_kind="FORECAST"),
        _unit("f3", knowledge_kind="FORECAST"),
        _unit("f4", knowledge_kind="FORECAST"),
        _unit("f5", knowledge_kind="FORECAST"),
    ], relations=[{"source_uid": "u1", "target_uid": "f2", "relation_type": "SUPERSEDES"}])
    # author-2：无 eligible 外部验证样本，FORECAST 也不足 5 条。
    video_c = _seed_video("c", "author-2", "作者乙")
    _seed_units(repo, video_c, "c", [
        _unit("w1", truth_status="NOT_CHECKED", external_verification_status="NOT_RUN"),
        _unit("w2", knowledge_kind="FORECAST"),
    ])


def test_factual_accuracy_and_sample_size(tmp_path):
    _seed_main(tmp_path)
    stats = SourceReliabilityService().compute("author-1")
    assert stats["source_type"] == "video_creator"
    assert stats["sample_size"] == 2
    assert stats["factual_accuracy"] == 0.5
    assert stats["video_count"] == 1


def test_compute_matches_author_name(tmp_path):
    _seed_main(tmp_path)
    stats = SourceReliabilityService().compute("作者甲")
    assert stats["sample_size"] == 2
    assert stats["factual_accuracy"] == 0.5


def test_forecast_score_from_relations_and_validated(tmp_path):
    _seed_main(tmp_path)
    stats = SourceReliabilityService().compute("author-1")
    # f1 lifecycle=VALIDATED + f2 被 SUPERSEDES 印证 → 2/5
    assert stats["forecast_score"] == 0.4


def test_forecast_score_none_when_sample_too_small(tmp_path):
    _seed_main(tmp_path)
    stats = SourceReliabilityService().compute("author-2")
    assert stats["forecast_score"] is None
    assert stats["factual_accuracy"] is None
    assert stats["sample_size"] == 0
    assert stats["reliability_score"] is None


def test_min_forecast_sample_configurable(tmp_path):
    _seed_main(tmp_path)
    stats = SourceReliabilityService(min_forecast_sample=2).compute("author-2")
    # author-2 有 1 条 FORECAST，仍不足 2 → None
    assert stats["forecast_score"] is None
    stats = SourceReliabilityService(min_forecast_sample=1).compute("author-2")
    assert stats["forecast_score"] == 0.0


def test_backfill_writes_source_reliability_score(tmp_path):
    _seed_main(tmp_path)
    result = SourceReliabilityService().backfill()
    assert result["units_updated"] == 8  # author-1 的 8 条 unit（reliability=0.5）
    assert result["sources"]["author-1"]["reliability_score"] == 0.5
    with session_scope() as session:
        rows = session.execute(
            select(KnowledgeUnit.knowledge_uid, KnowledgeUnit.source_reliability_score)
        ).all()
    scores = {uid: score for uid, score in rows}
    assert scores["u1"] == 0.5
    assert scores["f5"] == 0.5
    # author-2 无可靠性分数 → 不回填
    assert scores["w1"] is None

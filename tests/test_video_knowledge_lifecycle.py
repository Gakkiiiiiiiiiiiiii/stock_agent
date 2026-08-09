from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from engines.content.knowledge_lifecycle_service import KnowledgeLifecycleService
from storage.db import Base, SessionLocal
from storage.models.content import VideoAsset
from storage.models.knowledge import KnowledgeLifecycleAudit, KnowledgeUnit, KnowledgeVerification, VideoChapter
from storage.models.vector import VectorIndexMapping, VectorIndexTask


def configure_test_db(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'video_knowledge_lifecycle.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed_unit(
    *,
    lifecycle_status: str = "ACTIVE",
    valid_to: datetime | None = None,
    uid: str | None = None,
    support_status: str = "UNSUPPORTED",
    review_status: str = "UNREVIEWED",
) -> int:
    with SessionLocal() as session:
        suffix = uid or lifecycle_status.lower()
        video = VideoAsset(platform="bilibili", platform_video_id=f"BVLC{suffix}", bvid=f"BVLC{suffix}", url=f"https://example.com/{suffix}", title="生命周期测试")
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
            content_hash="chapter-hash",
            parser_version="test",
        )
        session.add(chapter)
        session.flush()
        unit = KnowledgeUnit(
            knowledge_uid=f"ku-{suffix}",
            source_video_id=video.id,
            source_chapter_id=chapter.id,
            primary_domain="MARKET",
            knowledge_kind="STATE",
            temporal_class="SNAPSHOT",
            expression_type="AUTHOR_EXPLICIT",
            subject_type="THEME",
            subject_key="券商",
            subject_name="券商",
            predicate_key="当前状态",
            statement="券商当前处于活跃状态",
            canonical_statement="券商当前处于活跃状态",
            as_of_time=datetime.now(UTC),
            valid_to=valid_to,
            lifecycle_status=lifecycle_status,
            verification_status="UNVERIFIED",
            support_status=support_status,
            review_status=review_status,
            conflict_key="theme:券商:state",
            conflict_group_id="cg1",
            content_hash=f"unit-hash-{suffix}",
            extractor_version="test",
        )
        session.add(unit)
        session.flush()
        mapping = VectorIndexMapping(
            postgres_table="knowledge_unit",
            postgres_id=unit.id,
            chunk_id=f"knowledge_unit_{unit.id}_0",
            qdrant_collection="financial_video_timed_v1_bge_m3",
            qdrant_point_id="point-1",
            index_status="indexed",
        )
        session.add(mapping)
        session.commit()
        return unit.id


def test_transition_unit_writes_audit_and_delete_vector_task():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="video-knowledge-lifecycle-", dir=temp_root))
    configure_test_db(tmp_path)
    unit_id = seed_unit()

    result = KnowledgeLifecycleService().transition_unit(
        unit_id,
        lifecycle_status="RETIRED",
        verification_status="REJECTED",
        reason="人工下线",
        operator="admin",
    )

    assert result is not None
    assert result["lifecycle_status"] == "RETIRED"
    assert result["verification_status"] == "REJECTED"
    assert result["lifecycle_audit"]["from_lifecycle_status"] == "ACTIVE"
    assert result["lifecycle_audit"]["vector_task_ids"]
    assert result["vector_tasks"][0]["task_type"] == "delete"
    with SessionLocal() as session:
        audit = session.execute(select(KnowledgeLifecycleAudit).where(KnowledgeLifecycleAudit.knowledge_unit_id == unit_id)).scalars().one()
        task = session.execute(select(VectorIndexTask).where(VectorIndexTask.postgres_id == unit_id)).scalars().one()
        assert audit.operator == "admin"
        assert audit.reason == "人工下线"
        assert task.task_type == "delete"
        assert task.target_collection == "financial_video_timed_v1_bge_m3"


def test_expire_due_units_marks_expired_and_enqueues_sync():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="video-knowledge-expire-", dir=temp_root))
    configure_test_db(tmp_path)
    unit_id = seed_unit(valid_to=datetime.now(UTC) - timedelta(days=1))

    result = KnowledgeLifecycleService().expire_due_units(now=datetime.now(UTC), limit=10)

    assert result["expired_count"] == 1
    assert result["items"][0]["id"] == unit_id
    assert result["items"][0]["lifecycle_status"] == "EXPIRED"
    # An unverified claim is removed from vector retrieval once lifecycle sync
    # runs; source location alone is not a retrieval entitlement.
    assert result["vector_tasks"][0]["task_type"] == "delete"
    with SessionLocal() as session:
        unit = session.get(KnowledgeUnit, unit_id)
        audit = session.execute(select(KnowledgeLifecycleAudit).where(KnowledgeLifecycleAudit.knowledge_unit_id == unit_id)).scalars().one()
        assert unit.lifecycle_status == "EXPIRED"
        assert audit.to_lifecycle_status == "EXPIRED"


def test_rejected_unit_enqueues_vector_delete_for_indexed_collection():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="video-knowledge-reject-", dir=temp_root))
    configure_test_db(tmp_path)
    unit_id = seed_unit(uid="reject")

    result = KnowledgeLifecycleService().transition_unit(unit_id, lifecycle_status="REJECTED", reason="证据不足")

    assert result["lifecycle_status"] == "REJECTED"
    assert result["vector_tasks"][0]["task_type"] == "delete"
    with SessionLocal() as session:
        task = session.execute(select(VectorIndexTask).where(VectorIndexTask.postgres_id == unit_id)).scalars().one()
        audit = session.execute(select(KnowledgeLifecycleAudit).where(KnowledgeLifecycleAudit.knowledge_unit_id == unit_id)).scalars().one()
        assert task.task_type == "delete"
        assert str(task.id) in audit.vector_task_ids_json


def test_expire_due_units_is_idempotent_for_already_expired_units():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="video-knowledge-expire-idempotent-", dir=temp_root))
    configure_test_db(tmp_path)
    seed_unit(valid_to=datetime.now(UTC) - timedelta(days=1), uid="expire-once")

    first = KnowledgeLifecycleService().expire_due_units(now=datetime.now(UTC), limit=10)
    second = KnowledgeLifecycleService().expire_due_units(now=datetime.now(UTC), limit=10)

    assert first["expired_count"] == 1
    assert second["expired_count"] == 0
    with SessionLocal() as session:
        tasks = session.execute(select(VectorIndexTask)).scalars().all()
        audits = session.execute(select(KnowledgeLifecycleAudit)).scalars().all()
        assert len(tasks) == 1
        assert len(audits) == 1


def test_expired_unit_hidden_from_current_state_but_kept_in_history():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="video-knowledge-history-", dir=temp_root))
    configure_test_db(tmp_path)
    seed_unit(lifecycle_status="EXPIRED", valid_to=datetime.now(UTC) - timedelta(days=1), uid="expired")

    service = KnowledgeLifecycleService()
    current = service.repository.get_current_subject_state("券商")
    history = service.repository.get_subject_history("券商")

    assert current["items"] == []
    assert [item["lifecycle_status"] for item in history["items"]] == ["EXPIRED"]


def test_verification_only_reject_enqueues_vector_delete():
    """P0-12 / §39：只改 review_status=REJECTED（lifecycle 不变）也必须 enqueue vector delete。"""
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="video-knowledge-review-reject-", dir=temp_root))
    configure_test_db(tmp_path)
    unit_id = seed_unit(uid="review-reject", support_status="SOURCE_SUPPORTED")

    result = KnowledgeLifecycleService().transition_unit(unit_id, review_status="REJECTED", reason="人工驳回", operator="admin")

    assert result is not None
    assert result["lifecycle_status"] == "ACTIVE"
    assert result["review_status"] == "REJECTED"
    assert result["vector_tasks"][0]["task_type"] == "delete"
    with SessionLocal() as session:
        unit = session.get(KnowledgeUnit, unit_id)
        task = session.execute(select(VectorIndexTask).where(VectorIndexTask.postgres_id == unit_id)).scalars().one()
        ledger = session.execute(
            select(KnowledgeVerification).where(
                KnowledgeVerification.knowledge_unit_id == unit_id,
                KnowledgeVerification.verifier_type == "MANUAL_REVIEW",
            )
        ).scalars().one()
        assert unit.review_status == "REJECTED"
        assert task.task_type == "delete"
        assert ledger.status == "REJECTED"


def test_manual_approval_does_not_forge_source_support():
    """P0-12 / §39：APPROVED 不改 support_status；OVERRIDDEN 缺 operator/reason 报 ValueError。"""
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="video-knowledge-manual-approve-", dir=temp_root))
    configure_test_db(tmp_path)
    unit_id = seed_unit(uid="manual-approve", support_status="SOURCE_LOCATED")

    service = KnowledgeLifecycleService()
    result = service.transition_unit(unit_id, review_status="APPROVED", reason="人工确认", operator="admin")

    assert result is not None
    assert result["review_status"] == "APPROVED"
    assert result["support_status"] == "SOURCE_LOCATED"
    with SessionLocal() as session:
        unit = session.get(KnowledgeUnit, unit_id)
        assert unit.support_status == "SOURCE_LOCATED"

    with pytest.raises(ValueError, match="OVERRIDDEN"):
        service.transition_unit(unit_id, review_status="OVERRIDDEN", operator="admin")
    with pytest.raises(ValueError, match="OVERRIDDEN"):
        service.transition_unit(unit_id, review_status="OVERRIDDEN", reason="覆盖")

    overridden = service.transition_unit(unit_id, review_status="OVERRIDDEN", reason="人工覆盖", operator="admin")
    assert overridden is not None
    assert overridden["review_status"] == "OVERRIDDEN"
    assert overridden["support_status"] == "SOURCE_LOCATED"

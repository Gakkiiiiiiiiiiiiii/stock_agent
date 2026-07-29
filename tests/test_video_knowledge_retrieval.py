from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from engines.retrieval.sparse_retriever import PostgresSparseRetriever
from storage.db import Base, SessionLocal
from storage.models.content import VideoAsset
from storage.models.knowledge import KnowledgeUnit, VideoChapter
from storage.models.vector import MemoryRecord, VectorIndexMapping


def configure_test_db(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'video_knowledge_retrieval.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_sparse_retriever_recalls_knowledge_unit_without_vector_mapping():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="video-knowledge-retrieval-", dir=temp_root))
    configure_test_db(tmp_path)
    with SessionLocal() as session:
        video = VideoAsset(platform="bilibili", platform_video_id="BVSR001", bvid="BVSR001", url="https://example.com", title="检索测试")
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
        session.add(
            KnowledgeUnit(
                knowledge_uid="ku-sparse",
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
                statement="券商 当前 状态 活跃",
                canonical_statement="券商 当前 状态 活跃",
                as_of_time=datetime.now(UTC),
                valid_to=datetime.now(UTC) + timedelta(days=1),
                lifecycle_status="ACTIVE",
                verification_status="UNVERIFIED",
                content_hash="unit-hash",
                extractor_version="test",
            )
        )
        session.commit()

    results = PostgresSparseRetriever().search(
        "券商 当前 状态",
        collections=["financial_video_timed_v1_bge_m3"],
        filters={"subject_key": "券商", "predicate_key": "当前状态", "valid_only": True},
        limit=5,
    )

    assert len(results) == 1
    assert results[0]["payload"]["postgres_table"] == "knowledge_unit"
    assert results[0]["payload"]["qdrant_collection"] == "financial_video_timed_v1_bge_m3"


def test_sparse_retriever_recalls_memory_record():
    tmp_path = _tmp_path("sparse-memory-")
    configure_test_db(tmp_path)
    with SessionLocal() as session:
        memory = MemoryRecord(
            memory_type="strategy",
            title="黄金高股息策略",
            content="黄金 高股息 机会 旧知识",
            source_type="research_note",
            status="validated",
            confidence=0.8,
        )
        session.add(memory)
        session.flush()
        session.add(
            VectorIndexMapping(
                postgres_table="memory_record",
                postgres_id=memory.id,
                chunk_id=f"memory_record_{memory.id}_0",
                qdrant_collection="financial_memory_v2_bge_m3",
                qdrant_point_id="memory-point",
                index_status="indexed",
            )
        )
        session.commit()

    results = PostgresSparseRetriever().search(
        "黄金 高股息",
        collections=["financial_memory_v2_bge_m3"],
        limit=5,
    )

    assert len(results) == 1
    assert results[0]["payload"]["postgres_table"] == "memory_record"
    assert results[0]["payload"]["qdrant_collection"] == "financial_memory_v2_bge_m3"
    assert results[0]["text"] == "黄金 高股息 机会 旧知识"


def test_sparse_retriever_merges_memory_and_knowledge_rows():
    tmp_path = _tmp_path("sparse-mixed-")
    configure_test_db(tmp_path)
    _seed_knowledge_unit("券商 当前 状态 活跃", subject_key="券商")
    with SessionLocal() as session:
        memory = MemoryRecord(
            memory_type="note",
            title="券商历史观察",
            content="券商 当前 状态 旧记忆",
            source_type="manual_note",
            status="validated",
            confidence=0.7,
        )
        session.add(memory)
        session.flush()
        session.add(
            VectorIndexMapping(
                postgres_table="memory_record",
                postgres_id=memory.id,
                chunk_id=f"memory_record_{memory.id}_0",
                qdrant_collection="financial_memory_v2_bge_m3",
                qdrant_point_id="memory-point",
                index_status="indexed",
            )
        )
        session.commit()

    results = PostgresSparseRetriever().search(
        "券商 当前 状态",
        collections=["financial_memory_v2_bge_m3", "financial_video_timed_v1_bge_m3"],
        limit=10,
    )

    tables = {item["payload"]["postgres_table"] for item in results}
    assert tables == {"memory_record", "knowledge_unit"}
    assert all(item["recall_sources"] == ["sparse"] for item in results)


def test_sparse_retriever_filters_invalid_and_expired_knowledge():
    tmp_path = _tmp_path("sparse-filter-")
    configure_test_db(tmp_path)
    _seed_knowledge_unit("券商 当前 状态 活跃", subject_key="券商", lifecycle_status="RETIRED")
    _seed_knowledge_unit("券商 当前 状态 过期", subject_key="券商", valid_to=datetime.now(UTC) - timedelta(days=1), uid="ku-expired")
    _seed_knowledge_unit("券商 当前 状态 有效", subject_key="券商", valid_to=datetime.now(UTC) + timedelta(days=1), uid="ku-active")

    valid_results = PostgresSparseRetriever().search(
        "券商 当前 状态",
        collections=["financial_video_timed_v1_bge_m3"],
        filters={"subject_key": "券商", "valid_only": True},
        limit=10,
    )
    historical_results = PostgresSparseRetriever().search(
        "券商 当前 状态",
        collections=["financial_video_timed_v1_bge_m3"],
        filters={"subject_key": "券商"},
        limit=10,
    )

    assert [item["payload"]["postgres_id"] for item in valid_results] == [valid_results[0]["payload"]["postgres_id"]]
    assert valid_results[0]["text"] == "券商 当前 状态 有效"
    assert {item["text"] for item in historical_results} == {"券商 当前 状态 过期", "券商 当前 状态 有效"}


def _tmp_path(prefix: str) -> Path:
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=temp_root))


def _seed_knowledge_unit(
    statement: str,
    *,
    subject_key: str,
    lifecycle_status: str = "ACTIVE",
    valid_to: datetime | None = None,
    uid: str = "ku-sparse",
) -> int:
    with SessionLocal() as session:
        video = VideoAsset(platform="bilibili", platform_video_id=f"BV{uid}", bvid=f"BV{uid}", url=f"https://example.com/{uid}", title="检索测试")
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
            knowledge_uid=uid,
            source_video_id=video.id,
            source_chapter_id=chapter.id,
            primary_domain="MARKET",
            knowledge_kind="STATE",
            temporal_class="SNAPSHOT",
            expression_type="AUTHOR_EXPLICIT",
            subject_type="THEME",
            subject_key=subject_key,
            subject_name=subject_key,
            predicate_key="当前状态",
            statement=statement,
            canonical_statement=statement,
            as_of_time=datetime.now(UTC),
            valid_to=valid_to,
            lifecycle_status=lifecycle_status,
            verification_status="UNVERIFIED",
            content_hash=f"unit-hash-{uid}",
            extractor_version="test",
        )
        session.add(unit)
        session.commit()
        return unit.id

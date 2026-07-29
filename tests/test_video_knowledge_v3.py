from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, select

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from engines.content.chapter_classifier import ChapterClassifier
from engines.content.video_ingest_service import VideoIngestService
from storage.db import Base, SessionLocal
from storage.models.content import FinancialEvent, VideoChunk, VideoSummary
from storage.models.vector import MemoryRecord, VectorIndexTask
from tests.test_content_service import FakeAsrService, FakeAudioPipeline, FakeBilibiliClient, FakeFrameExtractor, FakeVisionService


def configure_test_db(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'video_knowledge_v3.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_video_ingest_writes_v3_knowledge_without_video_memory():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="video-knowledge-v3-", dir=temp_root))
    configure_test_db(tmp_path)

    service = VideoIngestService(
        bilibili_client=FakeBilibiliClient(tmp_path),
        audio_pipeline=FakeAudioPipeline(),
        asr_service=FakeAsrService(),
        frame_extractor=FakeFrameExtractor(),
        vision_service=FakeVisionService(),
        storage_root=tmp_path / "content_storage",
    )

    try:
        queued = service.enqueue_bilibili(url="https://www.bilibili.com/video/BVTEST123")
        detail = service.process_task(queued["task_id"])

        assert detail["task"]["status"] == "success"
        assert detail["analysis_document"]["knowledge_unit_count"] >= 1
        assert len(detail["chapters"]) >= 1
        assert len(detail["knowledge_units"]) >= 1
        assert all(unit["evidence"] for unit in detail["knowledge_units"])
        assert any(unit["knowledge_kind"] in {"CAUSAL_THESIS", "RISK_CONDITION"} for unit in detail["knowledge_units"])

        with SessionLocal() as session:
            assert session.execute(select(VideoChunk)).scalars().all() == []
            assert session.execute(select(FinancialEvent)).scalars().all() == []
            assert session.execute(select(VideoSummary)).scalars().all() == []
            assert session.execute(select(MemoryRecord).where(MemoryRecord.source_type.in_(["bilibili_video_summary", "bilibili_video_viewpoint", "bilibili_financial_event"]))).scalars().all() == []
            tasks = session.execute(select(VectorIndexTask).where(VectorIndexTask.postgres_table == "knowledge_unit")).scalars().all()
            assert len(tasks) == len(detail["knowledge_units"])
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_chapter_classifier_does_not_treat_financial_attention_as_ad():
    result = ChapterClassifier().classify("黄金主题仍有催化，关注龙头股和风险控制。")
    assert result["chapter_type"] != "ADVERTISEMENT"

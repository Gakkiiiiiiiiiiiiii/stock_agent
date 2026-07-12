from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import create_engine

from engines.content.video_ingest_service import VideoIngestService
from storage.db import Base, SessionLocal
from storage.repositories.content_repository import ContentTaskRepository, VideoAssetRepository, VideoFrameRepository, VideoSummaryRepository
from storage.repositories.vector_repository import MemoryRepository, VectorMappingRepository


class FakeBilibiliClient:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve_source(self, url=None, bv_id=None):
        if bv_id:
            return f"https://www.bilibili.com/video/{bv_id}", bv_id
        return url, "BVTEST123"

    def fetch_metadata(self, url=None, bv_id=None):
        _ = (url, bv_id)
        return {
            "platform": "bilibili",
            "platform_video_id": "BVTEST123",
            "bvid": "BVTEST123",
            "url": "https://www.bilibili.com/video/BVTEST123",
            "title": "测试视频",
            "author_name": "测试UP",
            "author_id": "up_1",
            "publish_time": "20260711",
            "duration_seconds": 120,
            "cover_url": "https://example.com/cover.jpg",
            "description": "测试描述",
        }

    def download_audio(self, output_dir, url=None, bv_id=None):
        _ = (url, bv_id)
        path = Path(output_dir) / "BVTEST123.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake audio")
        return path

    def download_video(self, output_dir, url=None, bv_id=None):
        _ = (url, bv_id)
        path = Path(output_dir) / "BVTEST123.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake video")
        return path


class FakeAudioPipeline:
    def standardize_audio(self, input_path, output_dir):
        target = Path(output_dir) / "BVTEST123_16k_mono.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(input_path).read_bytes())
        return target

    def probe_duration_seconds(self, input_path):
        _ = input_path
        return 120.0


class ShortAudioPipeline(FakeAudioPipeline):
    def probe_duration_seconds(self, input_path):
        _ = input_path
        return 30.0


class FakeAsrService:
    def transcribe(self, audio_path, language_hint=None):
        _ = (audio_path, language_hint)
        return {
            "language": "zh",
            "provider": "fake_asr",
            "model": "tiny",
            "text": "黄金主题仍有催化，关注龙头股和风险控制。",
            "segments": [
                {
                    "segment_index": 0,
                    "start_ms": 0,
                    "end_ms": 5000,
                    "speaker_label": "speaker_0",
                    "text": "黄金主题仍有催化，关注龙头股和风险控制。",
                    "confidence_score": 0.91,
                }
            ],
        }


class FakeSummarizer:
    def __init__(self) -> None:
        self.last_visual_context = None
        self.last_chunks = None
        self.last_events = None
        self.last_video_type = None

    def summarize(self, metadata, transcript, mode="investment", visual_context=None, chunks=None, events=None, video_type=None):
        _ = (metadata, transcript, mode)
        self.last_visual_context = visual_context
        self.last_chunks = chunks
        self.last_events = events
        self.last_video_type = video_type
        return {
            "summary_mode": "investment",
            "summary_markdown": "# 测试视频\n\n黄金主题观点整理",
            "core_summary": "看多黄金主题，但强调节奏和风控。",
            "bull_points": ["避险情绪升温"],
            "bear_points": ["短线波动大"],
            "themes": ["黄金"],
            "symbols": ["600547"],
            "catalysts": ["金价上行"],
            "risks": ["追高风险"],
            "fact_points": ["PPI阶段性承压"],
            "forecast_points": ["下周仍有修复预期"],
            "invalidation_conditions": ["跌破关键缺口则观点失效"],
            "video_type": video_type or "MARKET_REVIEW",
            "chapter_summaries": ["[章节 1] 黄金与指数风险并行"],
            "actionable_view": "更适合回调跟踪",
            "evidence_segments": [{"start_ms": 0, "end_ms": 5000, "text": "黄金主题仍有催化"}],
            "confidence_score": 0.78,
            "llm_provider": "fake",
            "llm_model": "fake-model",
        }


class FakeQdrantClient:
    def __init__(self) -> None:
        self.deleted_calls: list[tuple[str, dict]] = []

    def delete_by_payload(self, collection, filters):
        self.deleted_calls.append((collection, filters))


class FakeFrameExtractor:
    def extract(self, video_path, output_dir, transcript_segments=None):
        _ = (video_path, transcript_segments)
        target = Path(output_dir) / "frame_000001.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake frame")
        return [
            {
                "frame_index": 1,
                "timestamp_ms": 1000,
                "image_path": str(target),
                "trigger_source": "cue",
            }
        ]


class FakeVisionService:
    def analyze_frames(self, metadata, transcript, frames):
        _ = (metadata, transcript)
        return [
            {
                **frames[0],
                "ocr_text": "上证指数 日线 缺口",
                "visual_summary": "画面展示指数缺口与均线压力，偏谨慎。",
                "related_text": "这里的缺口很关键",
                "themes": ["指数"],
                "symbols": [],
                "confidence_score": 0.88,
            }
        ]


def configure_test_db(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'content_test.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_video_ingest_service_processes_task(monkeypatch):
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="content-test-", dir=temp_root))
    configure_test_db(tmp_path)
    memory_calls = []

    def fake_write_memory(payload, target_collection="financial_knowledge", existing_memory_id=None):
        memory_calls.append(
            {
                "payload": payload,
                "target_collection": target_collection,
                "existing_memory_id": existing_memory_id,
            }
        )
        return {
            "memory_id": 77 if payload["memory_type"] == "media_summary" else 100 + len(memory_calls),
            "task_id": 88 + len(memory_calls),
            "target_collection": target_collection,
            "payload_title": payload["title"],
            "existing_memory_id": existing_memory_id,
        }

    monkeypatch.setattr("engines.content.video_ingest_service.write_memory_and_enqueue", fake_write_memory)
    monkeypatch.setattr(
        "engines.content.video_ingest_service.enqueue_memory_reindex",
        lambda memory_id, target_collection="financial_knowledge": {
            "memory_id": memory_id,
            "task_id": 999,
            "target_collection": target_collection,
        },
    )
    summarizer = FakeSummarizer()
    service = VideoIngestService(
        bilibili_client=FakeBilibiliClient(tmp_path),
        audio_pipeline=FakeAudioPipeline(),
        asr_service=FakeAsrService(),
        summarizer=summarizer,
        frame_extractor=FakeFrameExtractor(),
        vision_service=FakeVisionService(),
        storage_root=tmp_path / "content_storage",
    )

    try:
        queued = service.enqueue_bilibili(url="https://www.bilibili.com/video/BVTEST123")
        assert queued["status"] == "pending"

        detail = service.process_task(queued["task_id"])
        task = ContentTaskRepository().get(queued["task_id"])
        assert task is not None
        assert task.status == "success"
        assert detail["video"]["transcript_status"] == "success"
        assert detail["summary"]["themes"] == ["黄金"]
        assert detail["summary"]["memory_record_id"] == 77
        assert detail["segments"][0]["text"] == "黄金主题仍有催化，关注龙头股和风险控制。"
        assert detail["visual_frames"][0]["ocr_text"] == "上证指数 日线 缺口"
        assert detail["chunks"][0]["topic"] in {"黄金", "支撑", "风险", "上证指数", "未分类片段"} or detail["chunks"][0]["topic"]
        assert len(detail["events"]) >= 1
        assert summarizer.last_visual_context is not None
        assert summarizer.last_chunks is not None
        assert summarizer.last_events is not None
        assert "画面展示指数缺口与均线压力" in summarizer.last_visual_context["outline"]
        assert any(call["payload"]["memory_type"] == "media_viewpoint" for call in memory_calls)
        assert any(call["payload"]["source_type"] == "bilibili_video_viewpoint" for call in memory_calls)
        assert any(call["payload"]["memory_type"] == "media_event" for call in memory_calls)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_video_ingest_service_rejects_preview_only_download(monkeypatch):
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="content-preview-test-", dir=temp_root))
    configure_test_db(tmp_path)
    service = VideoIngestService(
        bilibili_client=FakeBilibiliClient(tmp_path),
        audio_pipeline=ShortAudioPipeline(),
        asr_service=FakeAsrService(),
        summarizer=FakeSummarizer(),
        storage_root=tmp_path / "content_storage",
    )

    try:
        queued = service.enqueue_bilibili(url="https://www.bilibili.com/video/BVTEST123")
        try:
            service.process_task(queued["task_id"])
            assert False, "expected preview-only audio download to fail"
        except RuntimeError as exc:
            message = str(exc)
            assert "Bilibili audio download looks incomplete" in message
            assert "scripts/login-bilibili.ps1" in message
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_build_viewpoint_memory_payloads_tags_conflicts_by_theme():
    payloads = VideoIngestService._build_viewpoint_memory_payloads(
        metadata={
            "bvid": "BVTEST123",
            "platform_video_id": "BVTEST123",
            "title": "测试视频",
            "publish_time": "20260711",
        },
        summary={
            "themes": ["黄金", "半导体"],
            "symbols": ["600547"],
            "bull_points": ["黄金主题仍有催化"],
            "bear_points": ["黄金短线波动加大"],
            "risks": ["半导体抱团可能补跌"],
            "actionable_view": "黄金更适合回调跟踪",
            "confidence_score": 0.8,
        },
        events=[],
    )
    assert len(payloads) == 4
    bull_payload = next(item for item in payloads if item["related_strategy"] == "viewpoint_bull")
    bear_payload = next(item for item in payloads if item["related_strategy"] == "viewpoint_bear")
    assert bull_payload["related_theme"] == "黄金"
    assert bear_payload["related_theme"] == "黄金"
    assert bull_payload["source_type"] == "bilibili_video_viewpoint"


def test_delete_video_summary_removes_markdown_and_vector_records(monkeypatch):
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="content-delete-test-", dir=temp_root))
    configure_test_db(tmp_path)

    video_repo = VideoAssetRepository()
    summary_repo = VideoSummaryRepository()
    memory_repo = MemoryRepository()
    mapping_repo = VectorMappingRepository()
    fake_qdrant = FakeQdrantClient()
    monkeypatch.setattr("engines.content.video_ingest_service.FinancialQdrantClient", lambda: fake_qdrant)

    service = VideoIngestService(storage_root=tmp_path / "content_storage")
    service.summary_exporter.export_root = (tmp_path / "knowledge_base" / "video_summaries").resolve()

    try:
        metadata = FakeBilibiliClient(tmp_path).fetch_metadata()
        asset = video_repo.upsert_metadata(metadata)
        summary_payload = FakeSummarizer().summarize(metadata=metadata, transcript={})
        summary = summary_repo.upsert(asset.id, summary_payload)
        markdown_path = service.summary_exporter.export(metadata=metadata, summary=summary_payload)

        summary_memory = memory_repo.create(
            memory_type="media_summary",
            title=metadata["title"],
            content="summary content",
            source_type="bilibili_video_summary",
            confidence=0.8,
            importance="high",
            status="validated",
        )
        summary_repo.set_memory_record(summary.id, summary_memory.id)
        bull_viewpoint = memory_repo.create(
            memory_type="media_viewpoint",
            title="BVTEST123｜观点｜看多｜黄金｜01",
            content="看多黄金",
            source_type="bilibili_video_viewpoint",
            related_theme="黄金",
            related_strategy="viewpoint_bull",
            confidence=0.8,
            importance="high",
            status="validated",
        )
        bear_viewpoint = memory_repo.create(
            memory_type="media_viewpoint",
            title="BVTEST123｜观点｜看空｜黄金｜01",
            content="看空黄金",
            source_type="bilibili_video_viewpoint",
            related_theme="黄金",
            related_strategy="viewpoint_bear",
            confidence=0.8,
            importance="high",
            status="validated",
        )
        for record in (summary_memory, bull_viewpoint, bear_viewpoint):
            mapping_repo.upsert(
                postgres_table="memory_record",
                postgres_id=record.id,
                chunk_id=f"memory_record_{record.id}_chunk_001",
                qdrant_collection="financial_knowledge",
                qdrant_point_id=f"point-{record.id}",
                content_hash=f"hash-{record.id}",
                embedding_model="deterministic-local",
                reranker_model="local-lexical-reranker",
            )

        result = service.delete_video_summary(asset.id)

        assert result is not None
        assert result["deleted"] is True
        assert result["removed_markdown"] is True
        assert not markdown_path.exists()
        assert summary_repo.get_for_video(asset.id) is None
        assert memory_repo.get(summary_memory.id).is_deleted is True
        assert memory_repo.get(bull_viewpoint.id).is_deleted is True
        assert memory_repo.get(bear_viewpoint.id).is_deleted is True
        assert mapping_repo.list_for_record("memory_record", summary_memory.id) == []
        assert mapping_repo.list_for_record("memory_record", bull_viewpoint.id) == []
        assert mapping_repo.list_for_record("memory_record", bear_viewpoint.id) == []
        assert len(fake_qdrant.deleted_calls) == 3
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_list_videos_includes_markdown_only_summaries():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="content-markdown-list-test-", dir=temp_root))
    configure_test_db(tmp_path)
    service = VideoIngestService(storage_root=tmp_path / "content_storage")
    service.summary_exporter.export_root = (tmp_path / "knowledge_base" / "video_summaries").resolve()
    service.query_repo.summary_exporter = service.summary_exporter

    try:
        export_root = service.summary_exporter.export_root
        export_root.mkdir(parents=True, exist_ok=True)
        markdown_path = export_root / "20260710_BV19qNj6SEAv_测试视频.md"
        markdown_path.write_text(
            "# 测试视频\n\n## 元信息\n- 作者：测试UP\n- 发布时间：20260710\n- 总结模型：deepseek / deepseek-v4-pro\n- 置信度：1.0\n",
            encoding="utf-8",
        )
        items = service.list_videos(limit=10)
        assert len(items) == 1
        assert items[0]["video_id"] is None
        assert items[0]["summary_source"] == "markdown_only"
        assert items[0]["summary_doc_path"] == "video_summaries/20260710_BV19qNj6SEAv_测试视频.md"
        assert items[0]["bvid"] == "BV19qNj6SEAv"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

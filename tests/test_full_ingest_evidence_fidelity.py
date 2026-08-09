"""P0-1 主链路 evidence 保真验收测试（设计文档 §5）。

跑 VideoIngestService.process_task 全链路（fake 外部依赖 + SQLite repository），
断言 evidence 元数据一路保留到 knowledge_evidence 表，而不是只测局部单元能力。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, select

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from engines.content.video_ingest_service import VideoIngestService
from storage.db import Base, SessionLocal
from storage.models.knowledge import KnowledgeEvidence
from tests.test_content_service import (
    FakeAnalysisDocumentModel,
    FakeAudioPipeline,
    FakeBilibiliClient,
    FakeFrameExtractor,
)


class FidelityAsrService:
    """带 word 级时间戳与 ASR 质量分的 fake ASR。"""

    def transcribe(self, audio_path, language_hint=None, speaker_mode="UNKNOWN"):
        _ = (audio_path, language_hint, speaker_mode)
        return {
            "language": "zh",
            "provider": "fake_asr",
            "model": "tiny",
            "text": "券商板块受流动性改善催化维持偏强，注意风险控制。",
            "segments": [
                {
                    "segment_index": 0,
                    "start_ms": 0,
                    "end_ms": 5000,
                    "text": "券商板块受流动性改善催化维持偏强，注意风险控制。",
                    "confidence_score": 0.91,
                    "avg_logprob": -0.12,
                    "no_speech_prob": 0.01,
                    "compression_ratio": 1.1,
                    "mean_word_probability": 0.88,
                    "min_word_probability": 0.61,
                    "word_timestamps": [
                        {"word": "券商", "start_ms": 0, "end_ms": 400, "probability": 0.93},
                        {"word": "板块", "start_ms": 400, "end_ms": 800, "probability": 0.9},
                    ],
                }
            ],
        }


class FakeDiarizationService:
    """fake diarization：给每个 segment 标注说话人。"""

    def annotate(self, audio_path, transcript, speaker_mode="DIARIZE"):
        _ = audio_path
        return transcript | {
            "segments": [
                dict(segment) | {"speaker_label": "speaker_0", "speaker_id": "speaker_0"}
                for segment in transcript.get("segments", [])
            ],
            "diarization": {"status": "COMPLETED", "model": "fake", "speaker_mode": speaker_mode, "speaker_count": 1},
        }


class FidelityVisionService:
    """带 OCR block（bbox/score）的 fake 视觉服务。"""

    def analyze_frames(self, metadata, transcript, frames):
        _ = (metadata, transcript)
        return [
            {
                **frames[0],
                "ocr_text": "上证指数 日线 缺口",
                "ocr_evidence": {
                    "text": "上证指数 日线 缺口",
                    "blocks": [{"text": "上证指数", "bbox": [10, 20, 110, 40], "score": 0.99}],
                },
                "ocr_confidence_score": 0.99,
                "visual_summary": "画面展示指数缺口与均线压力。",
                "themes": ["指数"],
                "symbols": [],
                "confidence_score": 0.88,
            }
        ]


class FidelityKnowledgeModel:
    """返回两条 unit：一条 ASR 证据、一条 OCR 证据。"""

    def available(self):
        return True

    def complete(self, **kwargs):
        _ = kwargs
        return {
            "provider": "fake",
            "model": "fake-k3",
            "content": (
                '{"units": ['
                '{"knowledge_kind": "CAUSAL_THESIS", "subject_key": "券商", "subject_name": "券商",'
                ' "predicate_key": "catalyst", "conclusion": "券商板块受流动性改善催化维持偏强。",'
                ' "claim_type": "OPINION", "sentiment": "BULLISH", "extraction_confidence": 0.9,'
                ' "entities": [{"entity_name": "券商", "entity_type": "THEME"}],'
                ' "evidence": [{"source_ref": "window_0"}]},'
                '{"knowledge_kind": "STATE", "subject_key": "指数", "subject_name": "指数",'
                ' "predicate_key": "visual_state", "conclusion": "画面显示指数处于关键压力区。",'
                ' "claim_type": "OPINION", "sentiment": "NEUTRAL", "extraction_confidence": 0.8,'
                ' "entities": [{"entity_name": "指数", "entity_type": "INDEX"}],'
                ' "evidence": [{"source_ref": "window_0", "source_type": "OCR", "evidence_text": "上证指数 日线 缺口"},'
                ' {"source_ref": "window_0"}]}'
                ']}'
            ),
        }


def configure_test_db(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'evidence_fidelity.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_full_ingest_preserves_evidence_metadata(monkeypatch):
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="evidence-fidelity-", dir=temp_root))
    configure_test_db(tmp_path)

    monkeypatch.setattr(
        "engines.content.video_ingest_service.write_memory_and_enqueue",
        lambda payload, target_collection="financial_knowledge", existing_memory_id=None: {
            "memory_id": 1,
            "task_id": 1,
            "target_collection": target_collection,
        },
    )
    monkeypatch.setattr(
        "engines.content.video_ingest_service.enqueue_memory_reindex",
        lambda memory_id, target_collection="financial_knowledge": {"memory_id": memory_id, "task_id": 2, "target_collection": target_collection},
    )

    service = VideoIngestService(
        bilibili_client=FakeBilibiliClient(tmp_path),
        audio_pipeline=FakeAudioPipeline(),
        asr_service=FidelityAsrService(),
        diarization_service=FakeDiarizationService(),
        frame_extractor=FakeFrameExtractor(),
        vision_service=FidelityVisionService(),
        storage_root=tmp_path / "content_storage",
    )
    service.knowledge_extractor.model_client = FidelityKnowledgeModel()
    service.analysis_document_generator.model_client = FakeAnalysisDocumentModel()

    try:
        queued = service.enqueue_bilibili(url="https://www.bilibili.com/video/BVTEST123", use_diarization=True)
        detail = service.process_task(queued["task_id"])
        assert detail["task"]["status"] == "success"

        units = detail["knowledge_units"]
        assert len(units) >= 2

        # list_units_for_video 用轻量 evidence 序列化；完整 evidence 字段走 search_units。
        full_units = service.knowledge_repo.search_units("", limit=50)
        full_evidence = [item for unit in full_units for item in unit["evidence"]]

        asr_evidence = [item for item in full_evidence if str(item.get("source_type") or "").upper() == "ASR"]
        assert asr_evidence, "应至少有一条 ASR 证据"
        for evidence in asr_evidence:
            assert evidence["raw_text"]
            assert evidence["normalized_text"]
            assert evidence["word_timestamps"] != []
            assert evidence["asr_metrics"] not in ({}, [])
            assert any(entry.get("confidence_score") for entry in evidence["asr_metrics"])

        ocr_evidence = [item for item in full_evidence if str(item.get("source_type") or "").upper() in {"OCR", "VISION", "FRAME"}]
        assert ocr_evidence, "开启视觉 fake 时应至少有一条 OCR 证据"
        for evidence in ocr_evidence:
            assert evidence["raw_text"]
            assert evidence["bbox"] != []
            assert evidence["ocr_metrics"] != {}
            assert evidence["ocr_metrics"]["line_count"] >= 1

        # 开启 diarization fake 时，说话人归属必须落到 knowledge_unit 上。
        assert all(unit["speaker_id"] is not None for unit in units)

        # 直接对表断言 JSON 列（§5 验收口径：!= "[]" / != "{}"）。
        with SessionLocal() as session:
            rows = session.execute(select(KnowledgeEvidence)).scalars().all()
        assert rows, "knowledge_evidence 表应有持久化证据"
        asr_rows = [row for row in rows if row.source_type == "ASR"]
        ocr_rows = [row for row in rows if row.source_type in {"OCR", "VISION", "FRAME"}]
        assert asr_rows and ocr_rows
        for row in asr_rows:
            assert row.raw_text
            assert row.normalized_text
            assert row.word_timestamps_json != "[]"
            assert row.asr_metrics_json != "{}"
        for row in ocr_rows:
            assert row.bbox_json != "[]"
            assert row.ocr_metrics_json != "{}"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

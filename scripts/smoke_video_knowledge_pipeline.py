from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy import create_engine, select

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from engines.content.video_ingest_service import VideoIngestService
from engines.retrieval.embedder import EmbeddingMetadata
from storage.db import Base, SessionLocal
from storage.models.knowledge import KnowledgeLifecycleAudit
from storage.models.vector import VectorIndexMapping, VectorIndexTask


class FakeBilibiliClient:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve_source(self, url=None, bv_id=None):
        resolved_bv = bv_id or "BVSMOKE123"
        return url or f"https://www.bilibili.com/video/{resolved_bv}", resolved_bv

    def fetch_metadata(self, url=None, bv_id=None):
        resolved_bv = bv_id or "BVSMOKE123"
        return {
            "platform": "bilibili",
            "platform_video_id": resolved_bv,
            "bvid": resolved_bv,
            "url": url or f"https://www.bilibili.com/video/{resolved_bv}",
            "title": "视频知识冒烟测试",
            "author_name": "smoke",
            "author_id": "smoke_up",
            "publish_time": "20260730",
            "duration_seconds": 180,
            "cover_url": "https://example.com/smoke.jpg",
            "description": "仿真视频，用于发布前视频知识链路冒烟。",
        }

    def download_audio(self, output_dir, url=None, bv_id=None):
        _ = (url, bv_id)
        path = Path(output_dir) / "BVSMOKE123.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"smoke audio")
        return path

    def download_video(self, output_dir, url=None, bv_id=None):
        _ = (url, bv_id)
        path = Path(output_dir) / "BVSMOKE123.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"smoke video")
        return path


class FakeAudioPipeline:
    def standardize_audio(self, input_path, output_dir):
        target = Path(output_dir) / "BVSMOKE123_16k_mono.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(input_path).read_bytes())
        return target

    def probe_duration_seconds(self, input_path):
        _ = input_path
        return 180.0


class FakeAsrService:
    def transcribe(self, audio_path, language_hint=None):
        _ = (audio_path, language_hint)
        sentences = [
            "黄金主题仍有催化，避险需求会继续提振龙头股。",
            "上证指数日线站上均线以后偏强，但如果跌破缺口这个判断就失效。",
            "如果黄金龙头回调到支撑位，可以考虑低吸，仓位不能太重。",
            "这个方法通常可以作为判断趋势强弱的辅助指标。",
        ]
        segments = []
        for index, text in enumerate(sentences):
            segments.append(
                {
                    "segment_index": index,
                    "start_ms": index * 5000,
                    "end_ms": (index + 1) * 5000,
                    "speaker_label": "speaker_0",
                    "text": text,
                    "confidence_score": 0.92,
                }
            )
        return {
            "language": "zh",
            "provider": "fake_asr",
            "model": "smoke-asr",
            "text": "\n".join(sentences),
            "segments": segments,
        }


class FakeFrameExtractor:
    def extract(self, video_path, output_dir, transcript_segments=None, chapters=None):
        _ = (video_path, transcript_segments, chapters)
        target = Path(output_dir) / "frame_000001.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake frame")
        return [
            {
                "frame_index": 1,
                "timestamp_ms": 6000,
                "image_path": str(target),
                "trigger_source": "smoke",
            }
        ]


class FakeKnowledgeModel:
    def available(self):
        return True

    def complete(self, **kwargs):
        _ = kwargs
        return {
            "provider": "fake",
            "model": "fake-k3",
            "content": (
                '{"units": ['
                '{"knowledge_kind": "CAUSAL_THESIS", "subject_key": "黄金", "subject_name": "黄金",'
                ' "predicate_key": "catalyst", "conclusion": "黄金主题受流动性改善催化维持偏强。",'
                ' "claim_type": "OPINION", "sentiment": "BULLISH", "extraction_confidence": 0.9,'
                ' "entities": [{"entity_name": "黄金", "entity_type": "THEME"}],'
                ' "evidence": [{"source_ref": "window_0"}]},'
                '{"knowledge_kind": "RISK_CONDITION", "subject_key": "黄金", "subject_name": "黄金",'
                ' "predicate_key": "risk_condition", "conclusion": "若跌破五日线则黄金偏强判断失效需减仓。",'
                ' "claim_type": "OPINION", "sentiment": "BEARISH", "extraction_confidence": 0.85,'
                ' "evidence": [{"source_ref": "window_0"}]}'
                ']}'
            ),
        }


class FakeVisionService:
    def analyze_frames(self, metadata, transcript, frames):
        _ = (metadata, transcript)
        return [
            {
                **frames[0],
                "ocr_text": "上证指数 日线 均线 缺口 支撑位",
                "visual_summary": "图表显示指数站上均线，缺口区域是关键证伪位置。",
                "related_text": "上证指数日线站上均线以后偏强，但如果跌破缺口这个判断就失效。",
                "themes": ["指数", "黄金"],
                "symbols": [],
                "confidence_score": 0.88,
            }
        ]


class FakeQdrantClient:
    def __init__(self) -> None:
        self.upserted: list[tuple[str, dict]] = []
        self.deleted: list[tuple[str, dict]] = []

    def ensure_collections(self) -> None:
        return None

    def upsert_chunk(self, collection: str, vector: list[float], payload: dict) -> str:
        _ = vector
        point_id = str(uuid5(NAMESPACE_URL, str(payload.get("chunk_id"))))
        self.upserted.append((collection, payload | {"point_id": point_id}))
        return point_id

    def delete_by_payload(self, collection: str, filters: dict) -> None:
        self.deleted.append((collection, filters))


class FakeEmbedder:
    @property
    def metadata(self) -> EmbeddingMetadata:
        return EmbeddingMetadata("sentence_transformers", "BAAI/bge-m3", 1024, True)

    def embed(self, text: str) -> list[float]:
        value = (sum(ord(ch) for ch in text) % 997) / 997.0
        return [value] * 1024


def run_smoke(root: Path, *, keep_db: bool = False) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="video-knowledge-smoke-", dir=root))
    db_path = work_dir / "smoke.db"
    warnings: list[str] = []
    try:
        engine = create_engine(
            f"sqlite:///{db_path}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        SessionLocal.configure(bind=engine)
        Base.metadata.create_all(bind=engine)

        service = VideoIngestService(
            bilibili_client=FakeBilibiliClient(work_dir),
            audio_pipeline=FakeAudioPipeline(),
            asr_service=FakeAsrService(),
            frame_extractor=FakeFrameExtractor(),
            vision_service=FakeVisionService(),
            storage_root=work_dir / "content_storage",
        )
        service.knowledge_extractor.model_client = FakeKnowledgeModel()
        queued = service.enqueue_bilibili(url="https://www.bilibili.com/video/BVSMOKE123")
        detail = service.process_task(queued["task_id"])
        video_id = int(detail["video"]["id"])
        task_id = int(detail["task"]["task_id"])
        units = detail.get("knowledge_units") or detail.get("knowledge_result", {}).get("knowledge_units") or []
        chapters = detail.get("chapters") or detail.get("knowledge_result", {}).get("chapters") or []
        if not units:
            raise AssertionError("no knowledge units were generated")
        missing_evidence = [unit["id"] for unit in units if not unit.get("evidence")]
        if missing_evidence:
            raise AssertionError(f"knowledge units missing evidence: {missing_evidence}")

        fake_qdrant = FakeQdrantClient()
        _process_vector_tasks(fake_qdrant)
        search = service.search_video_knowledge("黄金", limit=10)
        if not search["items"]:
            raise AssertionError("search_video_knowledge did not recall smoke units")
        subject_key = search["items"][0].get("subject_key") or units[0].get("subject_key")
        current = service.get_current_subject_state(subject_key, limit=10)
        history = service.get_subject_history(subject_key, limit=10)
        if not current["items"]:
            warnings.append("current_state_empty_before_lifecycle_update")
        unit_id = int(search["items"][0]["id"])
        retired = service.update_knowledge_unit_lifecycle(
            unit_id,
            lifecycle_status="RETIRED",
            verification_status="REJECTED",
            note="smoke retire check",
            operator="smoke",
        )
        if retired is None:
            raise AssertionError("lifecycle update returned no unit")
        delete_tasks = [task for task in retired.get("vector_tasks") or [] if task.get("task_type") == "delete"]
        if not delete_tasks:
            raise AssertionError("retiring a unit did not enqueue vector delete task")
        _process_vector_tasks(fake_qdrant)
        audits = service.list_knowledge_unit_lifecycle_audits(unit_id, limit=20)

        with SessionLocal() as session:
            vector_task_count = session.execute(select(VectorIndexTask)).scalars().all()
            mapping_count = len(session.execute(select(VectorIndexMapping)).scalars().all())
            audit_count = len(session.execute(select(KnowledgeLifecycleAudit)).scalars().all())

        quality = detail.get("quality_metrics") or detail.get("knowledge_result", {}).get("quality_metrics") or {}
        summary = {
            "ok": True,
            "video_id": video_id,
            "task_id": task_id,
            "chapter_count": len(chapters),
            "knowledge_unit_count": len(units),
            "evidence_coverage": round((len(units) - len(missing_evidence)) / max(len(units), 1), 4),
            "ocr_evidence_unit_count": quality.get("ocr_evidence_unit_count", 0),
            "low_evidence_unit_count": quality.get("low_evidence_unit_count", 0),
            "vector_task_count": len(vector_task_count),
            "vector_mapping_count": mapping_count,
            "fake_qdrant_upsert_count": len(fake_qdrant.upserted),
            "fake_qdrant_delete_count": len(fake_qdrant.deleted),
            "search_hit_count": len(search["items"]),
            "current_state_count": len(current["items"]),
            "history_count": len(history["items"]),
            "lifecycle_audit_count": audit_count,
            "latest_audit_count": len(audits["items"]),
            "warnings": warnings,
            "work_dir": str(work_dir) if keep_db else None,
        }
        if summary["chapter_count"] <= 0 or summary["knowledge_unit_count"] <= 0:
            raise AssertionError("empty chapter or knowledge output")
        if summary["fake_qdrant_upsert_count"] <= 0 or summary["fake_qdrant_delete_count"] <= 0:
            raise AssertionError("vector worker smoke did not upsert and delete")
        if summary["lifecycle_audit_count"] <= 0:
            raise AssertionError("lifecycle audit was not written")
        return summary
    finally:
        if not keep_db:
            shutil.rmtree(work_dir, ignore_errors=True)


def _process_vector_tasks(fake_qdrant: FakeQdrantClient) -> None:
    import workers.vector_index_worker as vector_worker

    vector_worker.FinancialQdrantClient = lambda: fake_qdrant
    vector_worker.build_embedder = lambda: FakeEmbedder()
    for _ in range(200):
        if not vector_worker.process_one_task():
            return
    raise RuntimeError("vector task queue did not drain within 200 iterations")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a simulated end-to-end smoke check for video KnowledgeUnit lifecycle.")
    parser.add_argument("--root", default=str(Path(".pytest-tmp").resolve()), help="Working directory for temporary smoke artifacts.")
    parser.add_argument("--keep-db", action="store_true", help="Keep the generated SQLite DB and content files for inspection.")
    args = parser.parse_args(argv)
    try:
        result = run_smoke(Path(args.root), keep_db=args.keep_db)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

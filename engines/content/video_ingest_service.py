from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from engines.content.asr_service import AsrService
from engines.content.audio_pipeline import AudioPipeline
from engines.content.bilibili_client import BilibiliClient
from engines.content.diarization_service import DiarizationService
from engines.content.event_conflict_resolver import EventConflictResolver
from engines.content.financial_entity_normalizer import FinancialEntityNormalizer
from engines.content.financial_event_extractor import FinancialEventExtractor
from engines.content.chapter_segmenter import ChapterSegmenter
from engines.content.knowledge_conflict_resolver import KnowledgeConflictResolver
from engines.content.knowledge_deduplicator import KnowledgeDeduplicator
from engines.content.knowledge_lifecycle_service import KnowledgeLifecycleService
from engines.content.knowledge_temporal_policy import KnowledgeTemporalPolicy
from engines.content.knowledge_unit_extractor import KnowledgeUnitExtractor
from engines.content.knowledge_unit_normalizer import KnowledgeUnitNormalizer
from engines.content.multimodal_context_builder import MultimodalContextBuilder
from engines.content.semantic_chunker import SemanticChunker
from engines.content.temporal_window_builder import TemporalWindowBuilder
from engines.content.video_analysis_document_generator import VideoAnalysisDocumentGenerator
from engines.content.video_frame_extractor import VideoFrameExtractor
from engines.content.video_ocr_service import VideoOcrService
from engines.content.video_summary_exporter import VideoSummaryMarkdownExporter
from engines.content.transcript_postprocessor import TranscriptPostprocessor
from engines.content.video_vision_service import VideoVisionService
from engines.content.video_summarizer import VideoSummarizer
from engines.content.xiaoe_hls_adapter import XiaoeHlsAdapter
from engines.memory.memory_writer import enqueue_memory_reindex, write_memory_and_enqueue
from engines.retrieval.qdrant_client import FinancialQdrantClient
from financial_agent.utils import project_root
from storage.repositories.content_repository import ContentQueryRepository, ContentTaskRepository, FinancialEventRepository, VideoAssetRepository, VideoChunkRepository, VideoFrameRepository, VideoSummaryRepository
from storage.repositories.knowledge_repository import (
    KnowledgeExtractionRunRepository,
    KnowledgeRepository,
    KnowledgeVectorTaskService,
    VideoAnalysisDocumentRepository,
)
from storage.repositories.vector_repository import MemoryRepository, VectorMappingRepository

logger = logging.getLogger(__name__)


class VideoIngestService:
    MAX_KNOWLEDGE_LIMIT = 200

    def __init__(
        self,
        bilibili_client: BilibiliClient | None = None,
        audio_pipeline: AudioPipeline | None = None,
        asr_service: AsrService | None = None,
        diarization_service: DiarizationService | None = None,
        transcript_postprocessor: TranscriptPostprocessor | None = None,
        summarizer: VideoSummarizer | None = None,
        semantic_chunker: SemanticChunker | None = None,
        entity_normalizer: FinancialEntityNormalizer | None = None,
        event_extractor: FinancialEventExtractor | None = None,
        conflict_resolver: EventConflictResolver | None = None,
        frame_extractor: VideoFrameExtractor | None = None,
        vision_service: VideoVisionService | None = None,
        multimodal_context_builder: MultimodalContextBuilder | None = None,
        video_repo: VideoAssetRepository | None = None,
        chunk_repo: VideoChunkRepository | None = None,
        event_repo: FinancialEventRepository | None = None,
        frame_repo: VideoFrameRepository | None = None,
        summary_repo: VideoSummaryRepository | None = None,
        task_repo: ContentTaskRepository | None = None,
        query_repo: ContentQueryRepository | None = None,
        storage_root: Path | None = None,
        summary_exporter: VideoSummaryMarkdownExporter | None = None,
        xiaoe_hls_client: XiaoeHlsAdapter | None = None,
        lifecycle_service: KnowledgeLifecycleService | None = None,
    ) -> None:
        self.bilibili_client = bilibili_client or BilibiliClient()
        self.xiaoe_hls_client = xiaoe_hls_client or XiaoeHlsAdapter()
        self.audio_pipeline = audio_pipeline or AudioPipeline()
        self.asr_service = asr_service or AsrService()
        self.diarization_service = diarization_service or DiarizationService()
        self.transcript_postprocessor = transcript_postprocessor or TranscriptPostprocessor()
        self.summarizer = summarizer or VideoSummarizer()
        self.semantic_chunker = semantic_chunker or SemanticChunker()
        self.entity_normalizer = entity_normalizer or FinancialEntityNormalizer()
        self.event_extractor = event_extractor or FinancialEventExtractor(entity_normalizer=self.entity_normalizer)
        self.conflict_resolver = conflict_resolver or EventConflictResolver()
        self.frame_extractor = frame_extractor or VideoFrameExtractor()
        self.vision_service = vision_service or VideoVisionService(ocr_service=VideoOcrService())
        self.multimodal_context_builder = multimodal_context_builder or MultimodalContextBuilder()
        self.temporal_window_builder = TemporalWindowBuilder()
        self.chapter_segmenter = ChapterSegmenter()
        self.knowledge_extractor = KnowledgeUnitExtractor()
        self.knowledge_normalizer = KnowledgeUnitNormalizer(entity_normalizer=self.entity_normalizer)
        self.knowledge_temporal_policy = KnowledgeTemporalPolicy()
        self.knowledge_deduplicator = KnowledgeDeduplicator()
        self.knowledge_conflict_resolver = KnowledgeConflictResolver()
        self.analysis_document_generator = VideoAnalysisDocumentGenerator()
        self.video_repo = video_repo or VideoAssetRepository()
        self.chunk_repo = chunk_repo or VideoChunkRepository()
        self.event_repo = event_repo or FinancialEventRepository()
        self.frame_repo = frame_repo or VideoFrameRepository()
        self.summary_repo = summary_repo or VideoSummaryRepository()
        self.knowledge_repo = KnowledgeRepository()
        self.analysis_document_repo = VideoAnalysisDocumentRepository()
        self.extraction_run_repo = KnowledgeExtractionRunRepository()
        self.knowledge_vector_task_service = KnowledgeVectorTaskService()
        self.lifecycle_service = lifecycle_service or KnowledgeLifecycleService(
            repository=self.knowledge_repo,
            vector_task_service=self.knowledge_vector_task_service,
        )
        self.task_repo = task_repo or ContentTaskRepository()
        self.query_repo = query_repo or ContentQueryRepository()
        self.memory_repo = MemoryRepository()
        self.summary_exporter = summary_exporter or VideoSummaryMarkdownExporter()
        self.query_repo.summary_exporter = self.summary_exporter
        root = storage_root or project_root() / os.getenv("CONTENT_STORAGE_DIR", "storage/content")
        self.storage_root = root.resolve()
        self.raw_audio_dir = self.storage_root / "raw_audio"
        self.raw_video_dir = self.storage_root / "raw_video"
        self.processed_audio_dir = self.storage_root / "processed_audio"
        self.frame_dir = self.storage_root / "video_frames"
        self.raw_audio_dir.mkdir(parents=True, exist_ok=True)
        self.raw_video_dir.mkdir(parents=True, exist_ok=True)
        self.processed_audio_dir.mkdir(parents=True, exist_ok=True)
        self.frame_dir.mkdir(parents=True, exist_ok=True)

    def enqueue_bilibili(
        self,
        url: str | None = None,
        bv_id: str | None = None,
        force_reprocess: bool = False,
        summary_mode: str = "investment",
        index_to_memory: bool = True,
        use_diarization: bool = False,
        language_hint: str | None = "zh",
        enable_visual_context: bool = True,
    ) -> dict:
        source_url, parsed_bv = self.bilibili_client.resolve_source(url=url, bv_id=bv_id)
        existing = self.video_repo.get_by_source(platform="bilibili", bvid=parsed_bv, platform_video_id=parsed_bv)
        if existing is not None and not force_reprocess:
            detail = self.query_repo.get_video_detail(existing.id, summary_mode=summary_mode)
            if detail and (detail.get("analysis_document") or detail.get("summary")):
                return {
                    "task_id": None,
                    "video_id": existing.id,
                    "status": "success",
                    "stage": "deduplicated",
                    "deduplicated": True,
                }
        options = {
            "url": source_url,
            "bv_id": parsed_bv,
            "summary_mode": summary_mode,
            "index_to_memory": index_to_memory,
            "use_diarization": use_diarization,
            "language_hint": language_hint,
            "enable_visual_context": enable_visual_context,
        }
        task = self.task_repo.create(source_type="bilibili", source_ref=source_url, options=options, video_id=existing.id if existing else None)
        return {"task_id": task.id, "video_id": task.video_id, "status": task.status, "stage": task.stage, "deduplicated": False}

    def enqueue_xiaoe_hls(
        self,
        *,
        m3u8_url: str,
        page_url: str | None = None,
        title: str | None = None,
        platform_video_id: str | None = None,
        headers: dict[str, str] | None = None,
        authorized_content: bool = False,
        force_reprocess: bool = False,
        summary_mode: str = "investment",
        index_to_memory: bool = True,
        use_diarization: bool = False,
        language_hint: str | None = "zh",
        enable_visual_context: bool = False,
        author_name: str | None = None,
        publish_time: str | None = None,
        duration_seconds: int | None = None,
        cover_url: str | None = None,
        description: str | None = None,
        engine: str = "ffmpeg-direct",
        quality: str = "best",
        workers: int = 4,
        timeout_seconds: int = 30,
    ) -> dict:
        if not authorized_content:
            raise ValueError("authorized_content=true is required for xiaoe hls ingestion")
        metadata = self.xiaoe_hls_client.build_metadata(
            m3u8_url=m3u8_url,
            page_url=page_url,
            title=title,
            platform_video_id=platform_video_id,
            author_name=author_name,
            publish_time=publish_time,
            duration_seconds=duration_seconds,
            cover_url=cover_url,
            description=description,
        )
        existing = self.video_repo.get_by_source(platform="xiaoe", platform_video_id=metadata["platform_video_id"])
        if existing is not None and not force_reprocess:
            detail = self.query_repo.get_video_detail(existing.id, summary_mode=summary_mode)
            if detail and (detail.get("analysis_document") or detail.get("summary")):
                return {
                    "task_id": None,
                    "video_id": existing.id,
                    "status": "success",
                    "stage": "deduplicated",
                    "deduplicated": True,
                }
        options = {
            "m3u8_url": m3u8_url,
            "page_url": page_url,
            "title": title,
            "platform_video_id": metadata["platform_video_id"],
            "headers": headers or {},
            "summary_mode": summary_mode,
            "index_to_memory": index_to_memory,
            "use_diarization": use_diarization,
            "language_hint": language_hint,
            "enable_visual_context": enable_visual_context,
            "author_name": author_name,
            "publish_time": publish_time,
            "duration_seconds": duration_seconds,
            "cover_url": cover_url,
            "description": description,
            "engine": engine,
            "quality": quality,
            "workers": workers,
            "timeout_seconds": timeout_seconds,
        }
        source_ref = page_url or metadata["url"]
        task = self.task_repo.create(source_type="xiaoe_hls", source_ref=source_ref, options=options, video_id=existing.id if existing else None)
        return {"task_id": task.id, "video_id": task.video_id, "status": task.status, "stage": task.stage, "deduplicated": False}

    def process_task(self, task_id: int) -> dict:
        task = self.task_repo.get(task_id)
        if task is None:
            raise FileNotFoundError(task_id)
        if task.source_type == "xiaoe_hls":
            return self._process_xiaoe_hls_task(task_id)
        options = self.task_repo.serialize(task, redact_sensitive=False)["options"]
        self.task_repo.update(task_id, status="processing", stage="fetch_meta", progress=5)
        video_id: int | None = task.video_id
        try:
            metadata = self.bilibili_client.fetch_metadata(url=options.get("url"), bv_id=options.get("bv_id"))
            asset = self.video_repo.upsert_metadata(metadata)
            video_id = asset.id
            self.task_repo.update(task_id, video_id=asset.id, stage="download_audio", progress=15)
            raw_audio_path = self.bilibili_client.download_audio(self.raw_audio_dir, url=options.get("url"), bv_id=options.get("bv_id"))
            standardized_audio_path = self.audio_pipeline.standardize_audio(raw_audio_path, self.processed_audio_dir)
            self._ensure_full_audio_download(metadata=metadata, audio_path=standardized_audio_path)
            self.video_repo.update_audio(asset.id, str(standardized_audio_path))
            self.task_repo.update(task_id, stage="asr", progress=45)
            transcript = self.asr_service.transcribe(standardized_audio_path, language_hint=options.get("language_hint"))
            if options.get("use_diarization"):
                self.task_repo.update(task_id, stage="diarization", progress=60)
                transcript = self.diarization_service.annotate(standardized_audio_path, transcript)
            self.task_repo.update(task_id, stage="postprocess", progress=70)
            transcript = self.transcript_postprocessor.normalize(transcript, metadata=metadata)
            transcript["source_hash"] = hashlib.sha256((transcript.get("text") or "").encode("utf-8")).hexdigest()
            self.video_repo.save_transcript(asset.id, transcript)
            visual_context = None
            frame_insights: list[dict] = []
            if options.get("enable_visual_context", True):
                self.task_repo.update(task_id, stage="visual_context", progress=72)
                visual_bundle = self._build_visual_context(
                    metadata=metadata,
                    transcript=transcript,
                    url=options.get("url"),
                    bv_id=options.get("bv_id"),
                    video_id=asset.id,
                )
                if visual_bundle is not None:
                    visual_context = visual_bundle.get("context")
                    frame_insights = visual_bundle.get("frame_insights") or []
            knowledge_result = self._build_video_knowledge(
                task_id=task_id,
                metadata=metadata,
                video_id=asset.id,
                transcript=transcript,
                frame_insights=frame_insights,
                index_knowledge=bool(options.get("index_to_memory", True)),
            )
            self.task_repo.update(task_id, status="success", stage="success", progress=100, video_id=asset.id)
            detail = self.query_repo.get_video_detail(asset.id, summary_mode=options.get("summary_mode", "investment")) or {}
            return detail | {
                "task": self.task_repo.serialize(self.task_repo.get(task_id)),
                "visual_context": visual_context,
                "video_type": knowledge_result["analysis_document"].get("video_type"),
                "knowledge_result": knowledge_result,
            }
        except Exception as exc:
            if video_id is not None:
                self.video_repo.mark_transcript_failed(video_id)
            self.task_repo.mark_failed(task_id, str(exc), stage=self.task_repo.get(task_id).stage if self.task_repo.get(task_id) else "failed")
            raise

    def _process_xiaoe_hls_task(self, task_id: int) -> dict:
        task = self.task_repo.get(task_id)
        if task is None:
            raise FileNotFoundError(task_id)
        options = self.task_repo.serialize(task, redact_sensitive=False)["options"]
        self.task_repo.update(task_id, status="processing", stage="fetch_meta", progress=5)
        video_id: int | None = task.video_id
        try:
            metadata = self.xiaoe_hls_client.build_metadata(
                m3u8_url=options["m3u8_url"],
                page_url=options.get("page_url"),
                title=options.get("title"),
                platform_video_id=options.get("platform_video_id"),
                author_name=options.get("author_name"),
                publish_time=options.get("publish_time"),
                duration_seconds=options.get("duration_seconds"),
                cover_url=options.get("cover_url"),
                description=options.get("description"),
            )
            asset = self.video_repo.upsert_metadata(metadata)
            video_id = asset.id
            headers = options.get("headers") or {}
            self.task_repo.update(task_id, video_id=asset.id, stage="download_audio", progress=15)
            raw_video_path = self.xiaoe_hls_client.download_video(
                self.raw_video_dir,
                m3u8_url=options["m3u8_url"],
                page_url=options.get("page_url"),
                headers=headers,
                output_stem=metadata["platform_video_id"],
                engine=options.get("engine", "ffmpeg-direct"),
                quality=options.get("quality", "best"),
                workers=int(options.get("workers") or 4),
                timeout_seconds=int(options.get("timeout_seconds") or 30),
            )
            standardized_audio_path = self.audio_pipeline.standardize_audio(raw_video_path, self.processed_audio_dir)
            self._ensure_full_audio_download(metadata=metadata, audio_path=standardized_audio_path)
            self.video_repo.update_audio(asset.id, str(standardized_audio_path))
            self.task_repo.update(task_id, stage="asr", progress=45)
            transcript = self.asr_service.transcribe(standardized_audio_path, language_hint=options.get("language_hint"))
            if options.get("use_diarization"):
                self.task_repo.update(task_id, stage="diarization", progress=60)
                transcript = self.diarization_service.annotate(standardized_audio_path, transcript)
            self.task_repo.update(task_id, stage="postprocess", progress=70)
            transcript = self.transcript_postprocessor.normalize(transcript, metadata=metadata)
            transcript["source_hash"] = hashlib.sha256((transcript.get("text") or "").encode("utf-8")).hexdigest()
            self.video_repo.save_transcript(asset.id, transcript)
            visual_context = None
            frame_insights: list[dict] = []
            if options.get("enable_visual_context", False):
                self.task_repo.update(task_id, stage="visual_context", progress=72)
                visual_bundle = self._build_xiaoe_visual_context(
                    metadata=metadata,
                    transcript=transcript,
                    m3u8_url=options["m3u8_url"],
                    headers=headers,
                    video_id=asset.id,
                )
                if visual_bundle is not None:
                    visual_context = visual_bundle.get("context")
                    frame_insights = visual_bundle.get("frame_insights") or []
            knowledge_result = self._build_video_knowledge(
                task_id=task_id,
                metadata=metadata,
                video_id=asset.id,
                transcript=transcript,
                frame_insights=frame_insights,
                index_knowledge=bool(options.get("index_to_memory", True)),
            )
            self.task_repo.update(task_id, status="success", stage="success", progress=100, video_id=asset.id)
            detail = self.query_repo.get_video_detail(asset.id, summary_mode=options.get("summary_mode", "investment")) or {}
            return detail | {
                "task": self.task_repo.serialize(self.task_repo.get(task_id)),
                "visual_context": visual_context,
                "video_type": knowledge_result["analysis_document"].get("video_type"),
                "knowledge_result": knowledge_result,
            }
        except Exception as exc:
            if video_id is not None:
                self.video_repo.mark_transcript_failed(video_id)
            self.task_repo.mark_failed(task_id, str(exc), stage=self.task_repo.get(task_id).stage if self.task_repo.get(task_id) else "failed")
            raise

    def _build_video_knowledge(
        self,
        *,
        task_id: int,
        metadata: dict,
        video_id: int,
        transcript: dict,
        frame_insights: list[dict],
        index_knowledge: bool,
    ) -> dict:
        source_hash = transcript.get("source_hash") or hashlib.sha256((transcript.get("text") or "").encode("utf-8")).hexdigest()
        run = self.extraction_run_repo.start(
            video_id=video_id,
            source_hash=source_hash,
            parser_version="v3.0-rule",
            extractor_version="v3.2-k3-json-mode",
            schema_version="v1",
        )
        extraction_validation: dict = {}
        try:
            self.task_repo.update(task_id, stage="build_temporal_windows", progress=72)
            windows = self.temporal_window_builder.build(transcript=transcript, frame_insights=frame_insights)
            self.task_repo.update(task_id, stage="chapter_segment", progress=76)
            chapters = self.chapter_segmenter.segment(windows)
            self.task_repo.update(task_id, stage="knowledge_extract", progress=82)
            units = self.knowledge_extractor.extract(metadata=metadata, chapters=chapters)
            extraction_validation = getattr(self.knowledge_extractor, "last_validation_report", {})
            logger.info("知识流水线 video_id=%s extract=%s", video_id, len(units))
            self.task_repo.update(task_id, stage="knowledge_normalize", progress=86)
            units = self.knowledge_normalizer.normalize(units, metadata=metadata)
            logger.info("知识流水线 video_id=%s normalize=%s", video_id, len(units))
            source_date = self.knowledge_normalizer.parse_source_datetime(metadata.get("publish_time"))
            self.task_repo.update(task_id, stage="temporal_policy", progress=89)
            units = self.knowledge_temporal_policy.apply(units, source_date=source_date)
            self.task_repo.update(task_id, stage="deduplicate_conflict", progress=92)
            units = self.knowledge_deduplicator.deduplicate(units)
            logger.info("知识流水线 video_id=%s dedup=%s", video_id, len(units))
            units, relations = self.knowledge_conflict_resolver.resolve(units)
            logger.info("知识流水线 video_id=%s conflict_resolve=%s relations=%s", video_id, len(units), len(relations))
            self._ensure_reparse_does_not_regress(
                existing_units=self.knowledge_repo.list_units_for_video(video_id),
                replacement_units=units,
            )
            self.task_repo.update(task_id, stage="generate_document", progress=94)
            analysis_payload = self.analysis_document_generator.generate(metadata=metadata, chapters=chapters, units=units)
            self._apply_chapter_summaries(chapters, analysis_payload.get("chapter_summaries") or [])
            self.task_repo.update(task_id, stage="persist_knowledge", progress=95)
            persisted = self.knowledge_repo.replace_video_knowledge(
                video_id=video_id,
                chapters=chapters,
                units=units,
                relations=relations,
            )
            persisted_units = self.knowledge_repo.list_units_for_video(video_id)
            quality_metrics = self._knowledge_quality_metrics(extraction_validation, persisted_units)
            self.analysis_document_repo.upsert(video_id, analysis_payload)
            vector_tasks = []
            if index_knowledge:
                self.task_repo.update(task_id, stage="index_knowledge", progress=99)
                vector_tasks = self.knowledge_vector_task_service.enqueue_units(persisted_units)
            self.extraction_run_repo.finish(
                run.id,
                status="success",
                stage="completed",
                chapter_count=len(chapters),
                knowledge_unit_count=len(units),
                degraded=False,
                metrics=quality_metrics,
            )
            return {
                "run_id": run.id,
                "chapters": self.knowledge_repo.list_chapters(video_id),
                "knowledge_units": persisted_units,
                "relations": relations,
                "persisted": persisted,
                "analysis_document": self.analysis_document_repo.get_for_video(video_id) or analysis_payload,
                "vector_tasks": vector_tasks,
                "quality_metrics": quality_metrics,
            }
        except Exception as exc:
            self.extraction_run_repo.finish(
                run.id,
                status="failed",
                stage="failed",
                chapter_count=0,
                knowledge_unit_count=0,
                degraded=True,
                error_message=str(exc),
                metrics={"extraction_validation": extraction_validation},
            )
            raise

    def get_task(self, task_id: int) -> dict | None:
        return self.task_repo.serialize(self.task_repo.get(task_id))

    def get_video_detail(self, video_id: int, summary_mode: str = "investment") -> dict | None:
        detail = self.query_repo.get_video_detail(video_id, summary_mode=summary_mode)
        if detail is None:
            return None
        events = detail.get("events") or []
        detail["event_timeline"] = self.conflict_resolver.build_timeline(events)
        detail["video_type"] = self._infer_video_type_from_events(events)
        latest_run = self.extraction_run_repo.latest_for_video(video_id)
        if latest_run:
            detail["latest_extraction_run"] = latest_run
            detail["quality_metrics"] = latest_run.get("metrics") or {}
        return detail

    @staticmethod
    def _knowledge_quality_metrics(extraction_validation: dict, persisted_units: list[dict]) -> dict:
        ocr_evidence_count = 0
        low_evidence_count = 0
        for unit in persisted_units:
            evidence = unit.get("evidence") or []
            if any(str(item.get("source_type") or "").upper() in {"OCR", "VISION", "FRAME"} for item in evidence):
                ocr_evidence_count += 1
            if unit.get("verification_status") == "NEEDS_REVIEW":
                low_evidence_count += 1
        return {
            "extraction_validation": extraction_validation or {},
            "ocr_evidence_unit_count": ocr_evidence_count,
            "low_evidence_unit_count": low_evidence_count,
            "knowledge_unit_count": len(persisted_units),
        }

    @staticmethod
    def _ensure_reparse_does_not_regress(existing_units: list[dict], replacement_units: list[dict]) -> None:
        """Reject an anomalously sparse reparse before it can delete useful prior knowledge."""
        existing_count = len(existing_units)
        replacement_count = len(replacement_units)
        minimum_acceptable = max(4, math.ceil(existing_count * 0.5))
        if existing_count >= 8 and replacement_count < minimum_acceptable:
            raise RuntimeError(
                "知识重解析结果异常稀疏："
                f"原有 {existing_count} 条，新结果仅 {replacement_count} 条（至少需要 {minimum_acceptable} 条）；"
                "已保留原有知识，未执行覆盖。"
            )

    @staticmethod
    def _apply_chapter_summaries(chapters: list[dict], summaries: list[dict]) -> None:
        summary_by_index = {
            int(item.get("chapter_index") or 0): item
            for item in summaries
            if isinstance(item, dict) and str(item.get("summary") or "").strip()
        }
        for chapter in chapters:
            index = int(chapter.get("chapter_index") or 0)
            if item := summary_by_index.get(index):
                chapter["summary"] = str(item.get("summary") or "").strip()
                if title := str(item.get("title") or "").strip():
                    chapter["title"] = title

    def list_videos(self, summary_mode: str = "investment", limit: int = 50) -> list[dict]:
        return self.query_repo.list_videos(summary_mode=summary_mode, limit=limit)

    def get_video_summary_document(self, video_id: int, summary_mode: str = "investment") -> dict | None:
        return self.query_repo.get_video_summary_document(video_id, summary_mode=summary_mode)

    def delete_video_summary_by_path(self, summary_path: str, summary_mode: str = "investment", target_collection: str = "financial_knowledge") -> dict | None:
        matched = self.query_repo.find_video_by_summary_path(summary_path, summary_mode=summary_mode)
        if matched is None:
            return None
        return self.delete_video_summary(
            matched["video_id"],
            summary_mode=summary_mode,
            target_collection=target_collection,
        )

    def delete_video_summary(self, video_id: int, summary_mode: str = "investment", target_collection: str = "financial_knowledge") -> dict | None:
        detail = self.query_repo.get_video_detail(video_id, summary_mode=summary_mode)
        if detail is None or detail.get("summary") is None:
            return None

        video = detail["video"]
        summary = detail["summary"]
        summary_memory_id = summary.get("memory_record_id")
        title_prefix = f"{video.get('bvid') or video.get('platform_video_id') or 'video'}｜观点｜"
        viewpoint_records = self.memory_repo.list_by_title_prefix(
            source_type="bilibili_video_viewpoint",
            title_prefix=title_prefix,
        )
        event_title_prefix = f"{video.get('bvid') or video.get('platform_video_id') or 'video'}｜事件｜"
        event_records = self.memory_repo.list_by_title_prefix(
            source_type="bilibili_financial_event",
            title_prefix=event_title_prefix,
        )

        deleted_memory_ids: list[int] = []
        if summary_memory_id:
            result = self._delete_memory_record(summary_memory_id, fallback_collection=target_collection)
            if result.get("deleted"):
                deleted_memory_ids.append(summary_memory_id)

        for record in viewpoint_records:
            result = self._delete_memory_record(record.id, fallback_collection=target_collection)
            if result.get("deleted"):
                deleted_memory_ids.append(record.id)
        for record in event_records:
            result = self._delete_memory_record(record.id, fallback_collection=target_collection)
            if result.get("deleted"):
                deleted_memory_ids.append(record.id)

        export_path = detail.get("summary_export_path")
        removed_markdown = False
        if export_path:
            path = Path(export_path)
            if path.exists():
                path.unlink()
                removed_markdown = True

        deleted = self.summary_repo.delete_for_video(video_id, mode=summary_mode)
        return {
            "deleted": deleted,
            "video_id": video_id,
            "summary_mode": summary_mode,
            "removed_markdown": removed_markdown,
            "removed_markdown_path": export_path,
            "deleted_memory_ids": deleted_memory_ids,
            "deleted_viewpoint_memory_count": len(viewpoint_records),
            "deleted_event_memory_count": len(event_records),
            "deleted_summary_memory_id": summary_memory_id,
        }

    def get_video_segments(self, video_id: int) -> dict | None:
        detail = self.query_repo.get_video_detail(video_id)
        if detail is None:
            return None
        return {"video_id": video_id, "segments": detail["segments"]}

    def get_video_events(self, video_id: int, summary_mode: str = "investment") -> dict | None:
        detail = self.query_repo.get_video_detail(video_id, summary_mode=summary_mode)
        if detail is None:
            return None
        timeline = self.conflict_resolver.build_timeline(detail.get("events") or [])
        return {
            "video_id": video_id,
            "chunks": detail.get("chunks") or [],
            "events": detail.get("events") or [],
            "timeline": timeline,
        }

    def get_video_chapters(self, video_id: int) -> dict | None:
        detail = self.query_repo.get_video_detail(video_id)
        if detail is None:
            return None
        return {"video_id": video_id, "chapters": detail.get("chapters") or []}

    def get_video_chapter(self, video_id: int, chapter_id: int) -> dict | None:
        detail = self.query_repo.get_video_detail(video_id)
        if detail is None:
            return None
        return self.knowledge_repo.get_chapter(video_id, chapter_id)

    def list_video_knowledge_units(self, video_id: int, filters: dict | None = None, limit: int | None = None) -> dict | None:
        detail = self.query_repo.get_video_detail(video_id)
        if detail is None:
            return None
        safe_limit, warnings = self._safe_knowledge_limit(limit, default=100)
        return {
            "video_id": video_id,
            "items": self.knowledge_repo.list_units_for_video(video_id, filters=filters, limit=safe_limit),
            "limit": safe_limit,
            "next_cursor": None,
            "filters": filters or {},
            "warnings": warnings,
        }

    def get_knowledge_unit(self, unit_id: int) -> dict | None:
        return self.knowledge_repo.get_unit(unit_id)

    def search_video_knowledge(self, query: str, filters: dict | None = None, limit: int = 20) -> dict:
        safe_limit, warnings = self._safe_knowledge_limit(limit, default=20)
        return {
            "query": query,
            "items": self.knowledge_repo.search_units(query, filters=filters, limit=safe_limit),
            "limit": safe_limit,
            "next_cursor": None,
            "filters": filters or {},
            "warnings": warnings,
        }

    def update_knowledge_unit_lifecycle(
        self,
        unit_id: int,
        *,
        lifecycle_status: str | None = None,
        verification_status: str | None = None,
        valid_to: datetime | None = None,
        note: str | None = None,
        operator: str | None = None,
    ) -> dict | None:
        return self.lifecycle_service.transition_unit(
            unit_id,
            lifecycle_status=lifecycle_status,
            verification_status=verification_status,
            valid_to=valid_to,
            reason=note,
            operator=operator,
        )

    def list_knowledge_conflicts(self, subject_key: str | None = None, limit: int = 50) -> dict:
        safe_limit, warnings = self._safe_knowledge_limit(limit, default=50)
        payload = self.lifecycle_service.list_conflicts(subject_key=subject_key, limit=safe_limit)
        return payload | {"limit": safe_limit, "next_cursor": None, "filters": {"subject_key": subject_key} if subject_key else {}, "warnings": warnings}

    def expire_due_knowledge_units(self, now: datetime | None = None, limit: int = 500) -> dict:
        safe_limit, warnings = self._safe_knowledge_limit(limit, default=500, maximum=1000)
        payload = self.lifecycle_service.expire_due_units(now=now, limit=safe_limit)
        return payload | {"limit": safe_limit, "warnings": [*(payload.get("warnings") or []), *warnings]}

    def list_knowledge_unit_lifecycle_audits(self, unit_id: int, limit: int = 50) -> dict:
        safe_limit, warnings = self._safe_knowledge_limit(limit, default=50)
        payload = self.lifecycle_service.list_unit_audits(unit_id, limit=safe_limit)
        return payload | {"limit": safe_limit, "next_cursor": None, "warnings": warnings}

    def get_current_subject_state(self, subject_key: str, domain: str | None = None, limit: int = 20) -> dict:
        safe_limit, warnings = self._safe_knowledge_limit(limit, default=20)
        payload = self.knowledge_repo.get_current_subject_state(subject_key=subject_key, domain=domain, limit=safe_limit)
        return payload | {"limit": safe_limit, "next_cursor": None, "filters": {"subject_key": subject_key, "domain": domain}, "warnings": warnings}

    def get_subject_history(self, subject_key: str, domain: str | None = None, limit: int = 50) -> dict:
        safe_limit, warnings = self._safe_knowledge_limit(limit, default=50)
        payload = self.knowledge_repo.get_subject_history(subject_key=subject_key, domain=domain, limit=safe_limit)
        return payload | {"limit": safe_limit, "next_cursor": None, "filters": {"subject_key": subject_key, "domain": domain}, "warnings": warnings}

    @classmethod
    def _safe_knowledge_limit(cls, value: int | None, *, default: int, maximum: int | None = None) -> tuple[int, list[str]]:
        warnings = []
        max_value = maximum or cls.MAX_KNOWLEDGE_LIMIT
        try:
            limit = int(value if value is not None else default)
        except (TypeError, ValueError):
            limit = default
            warnings.append("invalid_limit_defaulted")
        if limit <= 0:
            limit = default
            warnings.append("non_positive_limit_defaulted")
        if limit > max_value:
            limit = max_value
            warnings.append(f"limit_clamped_to_{max_value}")
        return limit, warnings

    def reparse_video_knowledge(self, video_id: int, index_knowledge: bool = True) -> dict | None:
        detail = self.query_repo.get_video_detail(video_id)
        if detail is None:
            return None
        video = detail["video"]
        segments = detail.get("segments") or []
        transcript = {
            "text": "\n".join(segment.get("text") or "" for segment in segments).strip() or video.get("transcript_text") or "",
            "segments": segments,
            "language": video.get("transcript_language"),
            "provider": video.get("asr_provider"),
            "model": video.get("asr_model"),
        }
        if not transcript["text"]:
            raise RuntimeError("video has no transcript to reparse")
        transcript["source_hash"] = hashlib.sha256(transcript["text"].encode("utf-8")).hexdigest()
        metadata = {
            "platform": video.get("platform"),
            "platform_video_id": video.get("platform_video_id") or video.get("bvid"),
            "bvid": video.get("bvid"),
            "url": video.get("url") or "",
            "title": video.get("title") or "video",
            "author_name": video.get("author_name"),
            "author_id": video.get("author_id"),
            "publish_time": video.get("publish_time"),
            "duration_seconds": video.get("duration_seconds"),
            "cover_url": video.get("cover_url"),
            "description": video.get("description"),
        }
        task = self.task_repo.create(
            source_type="video_knowledge_reparse",
            source_ref=metadata["url"],
            options={"video_id": video_id, "parser": "v3.2-k3-json-mode", "index_knowledge": index_knowledge},
            video_id=video_id,
        )
        result = self._build_video_knowledge(
            task_id=task.id,
            metadata=metadata,
            video_id=video_id,
            transcript=transcript,
            frame_insights=[],
            index_knowledge=index_knowledge,
        )
        self.task_repo.update(task.id, status="success", stage="success", progress=100, video_id=video_id)
        return {"task": self.task_repo.serialize(self.task_repo.get(task.id)), "knowledge_result": result}

    def get_video_frame_image_path(self, video_id: int, frame_index: int) -> str | None:
        payload = self.frame_repo.get_for_video_frame(video_id, frame_index)
        if payload is None:
            return None
        return payload.get("image_path")

    def get_video_frame_image_path_by_filename(self, bvid: str, filename: str) -> str | None:
        normalized_bvid = str(bvid or "").strip()
        normalized_filename = Path(str(filename or "").strip()).name
        if not normalized_bvid or not normalized_filename:
            return None
        if normalized_filename != str(filename or "").strip():
            return None
        if not re.fullmatch(r"BV[0-9A-Za-z]+", normalized_bvid, flags=re.IGNORECASE):
            return None
        if not re.fullmatch(r"[0-9A-Za-z._-]+\.(?:jpg|jpeg|png|webp)", normalized_filename, flags=re.IGNORECASE):
            return None
        candidate = (self.frame_dir / normalized_bvid / normalized_filename).resolve()
        parent = (self.frame_dir / normalized_bvid).resolve()
        if parent not in candidate.parents:
            return None
        if not candidate.exists() or not candidate.is_file():
            return None
        return str(candidate)

    def _build_provisional_chapters(self, transcript: dict) -> list[dict]:
        """抽帧前基于转写切出临时主题章节，用于按章节分配帧预算。"""
        try:
            windows = self.temporal_window_builder.build(transcript=transcript, frame_insights=[])
            return self.chapter_segmenter.segment(windows)
        except Exception:
            logger.warning("临时章节切分失败，抽帧降级为均匀采样", exc_info=True)
            return []

    def _build_visual_context(
        self,
        metadata: dict,
        transcript: dict,
        url: str | None,
        bv_id: str | None,
        video_id: int,
    ) -> dict | None:
        try:
            video_path = self.bilibili_client.download_video(self.raw_video_dir, url=url, bv_id=bv_id)
            frame_output_dir = self.frame_dir / str(metadata.get("bvid") or metadata.get("platform_video_id") or video_id)
            chapters = self._build_provisional_chapters(transcript)
            frames = self.frame_extractor.extract(
                video_path=video_path,
                output_dir=frame_output_dir,
                transcript_segments=transcript.get("segments") or [],
                chapters=chapters,
            )
            frame_insights = self.vision_service.analyze_frames(metadata=metadata, transcript=transcript, frames=frames)
            self.frame_repo.replace_for_video(video_id, frame_insights)
            if not frame_insights:
                logger.warning("bilibili 视觉上下文为空: video_id=%s bvid=%s", video_id, bv_id)
                return None
            return {
                "context": self.multimodal_context_builder.build(transcript=transcript, frame_insights=frame_insights),
                "frame_insights": frame_insights,
            }
        except Exception:
            logger.warning("bilibili 视觉上下文构建失败: video_id=%s bvid=%s", video_id, bv_id, exc_info=True)
            return None

    def _build_xiaoe_visual_context(
        self,
        metadata: dict,
        transcript: dict,
        m3u8_url: str,
        headers: dict[str, str],
        video_id: int,
    ) -> dict | None:
        try:
            video_path = self.xiaoe_hls_client.download_video(
                self.raw_video_dir,
                m3u8_url=m3u8_url,
                page_url=str(metadata.get("url") or ""),
                headers=headers,
                output_stem=str(metadata.get("platform_video_id") or video_id),
            )
            frame_output_dir = self.frame_dir / str(metadata.get("platform_video_id") or video_id)
            chapters = self._build_provisional_chapters(transcript)
            frames = self.frame_extractor.extract(
                video_path=video_path,
                output_dir=frame_output_dir,
                transcript_segments=transcript.get("segments") or [],
                chapters=chapters,
            )
            frame_insights = self.vision_service.analyze_frames(metadata=metadata, transcript=transcript, frames=frames)
            self.frame_repo.replace_for_video(video_id, frame_insights)
            if not frame_insights:
                logger.warning("xiaoe 视觉上下文为空: video_id=%s platform_video_id=%s", video_id, metadata.get("platform_video_id"))
                return None
            return {
                "context": self.multimodal_context_builder.build(transcript=transcript, frame_insights=frame_insights),
                "frame_insights": frame_insights,
            }
        except Exception:
            logger.warning("xiaoe 视觉上下文构建失败: video_id=%s platform_video_id=%s", video_id, metadata.get("platform_video_id"), exc_info=True)
            return None

    def _enrich_chunks(self, chunks: list[dict]) -> list[dict]:
        enriched = []
        for chunk in chunks:
            item = dict(chunk)
            existing_entities = list(item.get("entities") or [])
            extracted_entities = self.entity_normalizer.extract_entities(
                item.get("topic") or "",
                item.get("transcript_text") or "",
                item.get("ocr_text") or "",
            )
            for entity in extracted_entities:
                ticker = str(entity.get("ticker") or entity.get("name") or "").strip()
                if ticker and ticker not in existing_entities:
                    existing_entities.append(ticker)
            item["entities"] = existing_entities
            enriched.append(item)
        return enriched

    def _delete_memory_record(self, memory_id: int, fallback_collection: str) -> dict:
        record = self.memory_repo.get(memory_id)
        if record is None:
            return {"deleted": False, "memory_id": memory_id, "missing": True}

        mapping_repo = VectorMappingRepository()
        mappings = mapping_repo.list_for_record("memory_record", memory_id)
        collections = sorted({mapping.qdrant_collection for mapping in mappings if mapping.qdrant_collection}) or [fallback_collection]
        qdrant = FinancialQdrantClient()
        for collection in collections:
            qdrant.delete_by_payload(collection, {"postgres_table": "memory_record", "postgres_id": memory_id})
        mapping_repo.delete_for_record("memory_record", memory_id)
        self.memory_repo.mark_deleted(memory_id)
        return {
            "deleted": True,
            "memory_id": memory_id,
            "collections": collections,
        }

    def _ensure_full_audio_download(self, metadata: dict, audio_path: Path) -> None:
        expected_duration = float(metadata.get("duration_seconds") or 0)
        if expected_duration <= 0:
            return
        audio_duration = float(self.audio_pipeline.probe_duration_seconds(audio_path))
        minimum_ratio = 0.8
        if audio_duration >= expected_duration * minimum_ratio:
            return
        platform = str(metadata.get("platform") or "video")
        platform_label = "Bilibili" if platform == "bilibili" else platform
        auth_source = getattr(self.bilibili_client, "describe_auth_source", lambda: "anonymous")()
        auth_hint = (
            "For charged or member-only Bilibili videos, run scripts/login-bilibili.ps1 to generate a project cookie file."
            if platform == "bilibili"
            else "Verify that the supplied media URL and headers are still authorized and not expired."
        )
        raise RuntimeError(
            f"{platform_label} audio download looks incomplete. "
            f"Expected about {expected_duration:.0f}s but only fetched {audio_duration:.0f}s. "
            f"Current auth source: {auth_source}. "
            f"{auth_hint}"
        )

    @staticmethod
    def _build_memory_payload(metadata: dict, summary: dict, markdown_path: Path | None = None) -> dict:
        themes = ", ".join(summary.get("themes", []))
        symbols = ", ".join(summary.get("symbols", []))
        source_date = VideoIngestService._parse_source_datetime(metadata.get("publish_time"))
        valid_from = source_date or datetime.now(UTC)
        content_parts = [
            f"视频标题：{metadata.get('title', '')}",
            f"作者：{metadata.get('author_name', '')}",
            f"发布时间：{metadata.get('publish_time', '')}",
            f"核心摘要：{summary.get('core_summary', '')}",
            f"主题：{themes}",
            f"标的：{symbols}",
            f"催化：{'；'.join(summary.get('catalysts', []))}",
            f"风险：{'；'.join(summary.get('risks', []))}",
            f"操作观点：{summary.get('actionable_view', '')}",
            "时效规则：若与旧结论冲突，优先采用发布时间更近的视频总结。",
        ]
        if markdown_path is not None:
            content_parts.append(f"Markdown归档：{markdown_path}")
        return {
            "memory_type": "media_summary",
            "title": metadata.get("title", "bilibili video summary"),
            "content": "\n".join(part for part in content_parts if part).strip(),
            "confidence": float(summary.get("confidence_score") or 0.5),
            "importance": "high",
            "status": "validated",
            "source_type": "bilibili_video_summary",
            "source_date": source_date,
            "valid_from": valid_from,
            "related_theme": summary.get("themes", [None])[0] if summary.get("themes") else None,
            "related_symbol": summary.get("symbols", [None])[0] if summary.get("symbols") else None,
        }

    def _sync_viewpoint_memories(self, metadata: dict, summary: dict, events: list[dict], target_collection: str) -> list[dict]:
        payloads = self._build_viewpoint_memory_payloads(metadata=metadata, summary=summary, events=events)
        source_type = "bilibili_video_viewpoint"
        title_prefix = f"{metadata.get('bvid') or metadata.get('platform_video_id') or 'video'}｜观点｜"
        existing_records = {
            record.title: record
            for record in self.memory_repo.list_by_title_prefix(source_type=source_type, title_prefix=title_prefix)
            if not record.is_deleted
        }
        synced: list[dict] = []
        current_titles = {payload["title"] for payload in payloads}
        for payload in payloads:
            existing = existing_records.get(payload["title"])
            synced.append(
                write_memory_and_enqueue(
                    payload,
                    target_collection=target_collection,
                    existing_memory_id=existing.id if existing else None,
                )
            )
        for title, record in existing_records.items():
            if title in current_titles:
                continue
            self.memory_repo.mark_deleted(record.id)
            enqueue_memory_reindex(record.id, target_collection=target_collection)
        self._sync_event_memories(metadata=metadata, events=events, target_collection=target_collection)
        return synced

    @staticmethod
    def _build_viewpoint_memory_payloads(metadata: dict, summary: dict, events: list[dict]) -> list[dict]:
        themes = [str(item).strip() for item in summary.get("themes", []) if str(item).strip()]
        symbols = [str(item).strip() for item in summary.get("symbols", []) if str(item).strip()]
        source_date = VideoIngestService._parse_source_datetime(metadata.get("publish_time"))
        valid_from = source_date or datetime.now(UTC)
        bvid = str(metadata.get("bvid") or metadata.get("platform_video_id") or "video")
        title = str(metadata.get("title") or "视频观点")
        payloads: list[dict] = []
        if events:
            for event in events:
                if event.get("conflict_status") == "superseded":
                    continue
                strategy_key = VideoIngestService._event_strategy_key(event)
                if not strategy_key:
                    continue
                strategy_label = {
                    "viewpoint_bull": "看多",
                    "viewpoint_bear": "看空",
                    "viewpoint_risk": "风险",
                    "viewpoint_actionable": "操作",
                }.get(strategy_key, "观点")
                statement = str(event.get("statement") or "").strip()
                if not statement:
                    continue
                related_symbol = VideoIngestService._first_event_symbol(event)
                related_theme = VideoIngestService._first_event_theme(event)
                topic_label = related_theme or related_symbol or VideoIngestService._compact_label(statement)
                event_order = int(event.get("event_index") or 0)
                payloads.append(
                    {
                        "memory_type": "media_viewpoint",
                        "title": f"{bvid}｜观点｜{strategy_label}｜{topic_label}｜{event_order:02d}",
                        "content": "\n".join(
                            [
                                f"来源视频：{title}",
                                f"视频编号：{bvid}",
                                f"发布时间：{metadata.get('publish_time', '')}",
                                f"视频时间轴：{event.get('start_ms', 0)}-{event.get('end_ms', 0)} ms",
                                f"观点类型：{strategy_label}",
                                f"观点主题：{related_theme or '未归类'}",
                                f"关联标的：{related_symbol or '未明确提及'}",
                                f"观点内容：{statement}",
                                f"条件：{event.get('condition_text') or '无'}",
                                f"证伪：{event.get('invalidation_text') or '无'}",
                                f"冲突状态：{event.get('conflict_status') or 'active'}",
                                "冲突处理：若同主题存在更新且方向冲突的观点，优先采用发布时间更近的观点。",
                            ]
                        ),
                        "source_type": "bilibili_video_viewpoint",
                        "source_date": source_date,
                        "valid_from": valid_from,
                        "related_theme": related_theme,
                        "related_symbol": related_symbol,
                        "related_strategy": strategy_key,
                        "status": "validated",
                        "importance": "high",
                        "confidence": float(event.get("confidence_score") or summary.get("confidence_score") or 0.5),
                    }
                )
            if payloads:
                return payloads
        viewpoint_buckets = [
            ("viewpoint_bull", "看多", summary.get("bull_points") or []),
            ("viewpoint_bear", "看空", summary.get("bear_points") or []),
            ("viewpoint_risk", "风险", summary.get("risks") or []),
        ]
        for strategy_key, strategy_label, items in viewpoint_buckets:
            for index, item in enumerate(items, start=1):
                text = str(item).strip()
                if not text:
                    continue
                related_theme = VideoIngestService._infer_viewpoint_theme(text=text, themes=themes)
                related_symbol = VideoIngestService._infer_viewpoint_symbol(text=text, symbols=symbols)
                topic_label = related_theme or related_symbol or VideoIngestService._compact_label(text)
                payloads.append(
                    {
                        "memory_type": "media_viewpoint",
                        "title": f"{bvid}｜观点｜{strategy_label}｜{topic_label}｜{index:02d}",
                        "content": "\n".join(
                            [
                                f"来源视频：{title}",
                                f"视频编号：{bvid}",
                                f"发布时间：{metadata.get('publish_time', '')}",
                                f"观点类型：{strategy_label}",
                                f"观点主题：{related_theme or '未归类'}",
                                f"关联标的：{related_symbol or '未明确提及'}",
                                f"观点内容：{text}",
                                "冲突处理：若同主题存在更新且方向冲突的观点，优先采用发布时间更近的观点。",
                            ]
                        ),
                        "source_type": "bilibili_video_viewpoint",
                        "source_date": source_date,
                        "valid_from": valid_from,
                        "related_theme": related_theme,
                        "related_symbol": related_symbol,
                        "related_strategy": strategy_key,
                        "status": "validated",
                        "importance": "high",
                        "confidence": float(summary.get("confidence_score") or 0.5),
                    }
                )
        actionable_view = str(summary.get("actionable_view") or "").strip()
        if actionable_view:
            related_theme = VideoIngestService._infer_viewpoint_theme(text=actionable_view, themes=themes)
            related_symbol = VideoIngestService._infer_viewpoint_symbol(text=actionable_view, symbols=symbols)
            payloads.append(
                {
                    "memory_type": "media_viewpoint",
                    "title": f"{bvid}｜观点｜操作｜{related_theme or related_symbol or '综合'}｜01",
                    "content": "\n".join(
                        [
                            f"来源视频：{title}",
                            f"视频编号：{bvid}",
                            f"发布时间：{metadata.get('publish_time', '')}",
                            "观点类型：操作",
                            f"观点主题：{related_theme or '综合'}",
                            f"关联标的：{related_symbol or '未明确提及'}",
                            f"观点内容：{actionable_view}",
                            "冲突处理：若同主题存在更新且方向冲突的观点，优先采用发布时间更近的观点。",
                        ]
                    ),
                    "source_type": "bilibili_video_viewpoint",
                    "source_date": source_date,
                    "valid_from": valid_from,
                    "related_theme": related_theme,
                    "related_symbol": related_symbol,
                    "related_strategy": "viewpoint_actionable",
                    "status": "validated",
                    "importance": "high",
                    "confidence": float(summary.get("confidence_score") or 0.5),
                }
            )
        return payloads

    def _sync_event_memories(self, metadata: dict, events: list[dict], target_collection: str) -> list[dict]:
        payloads = self._build_event_memory_payloads(metadata=metadata, events=events)
        source_type = "bilibili_financial_event"
        title_prefix = f"{metadata.get('bvid') or metadata.get('platform_video_id') or 'video'}｜事件｜"
        existing_records = {
            record.title: record
            for record in self.memory_repo.list_by_title_prefix(source_type=source_type, title_prefix=title_prefix)
            if not record.is_deleted
        }
        synced: list[dict] = []
        current_titles = {payload["title"] for payload in payloads}
        for payload in payloads:
            existing = existing_records.get(payload["title"])
            synced.append(
                write_memory_and_enqueue(
                    payload,
                    target_collection=target_collection,
                    existing_memory_id=existing.id if existing else None,
                )
            )
        for title, record in existing_records.items():
            if title in current_titles:
                continue
            self.memory_repo.mark_deleted(record.id)
            enqueue_memory_reindex(record.id, target_collection=target_collection)
        return synced

    @staticmethod
    def _infer_viewpoint_theme(text: str, themes: list[str]) -> str | None:
        normalized_text = VideoIngestService._normalize_topic_text(text)
        best_match: tuple[int, str] | None = None
        for theme in themes:
            score = 0
            for token in VideoIngestService._topic_tokens(theme):
                if token and token in normalized_text:
                    score += len(token)
            if score <= 0:
                continue
            if best_match is None or score > best_match[0]:
                best_match = (score, theme)
        return None if best_match is None else best_match[1]

    @staticmethod
    def _infer_viewpoint_symbol(text: str, symbols: list[str]) -> str | None:
        normalized_text = VideoIngestService._normalize_topic_text(text)
        for symbol in symbols:
            for token in VideoIngestService._topic_tokens(symbol):
                if token and token in normalized_text:
                    return symbol
        return None

    @staticmethod
    def _topic_tokens(value: str) -> list[str]:
        raw_tokens = re.split(r"[、，,；;（）()\[\]→\-·/\\\s]+", str(value).strip())
        tokens = [token for token in raw_tokens if len(token) >= 2]
        full_text = str(value).strip()
        if full_text and full_text not in tokens:
            tokens.insert(0, full_text)
        return tokens

    @staticmethod
    def _normalize_topic_text(value: str) -> str:
        return re.sub(r"\s+", "", str(value).strip().lower())

    @staticmethod
    def _compact_label(value: str, max_length: int = 18) -> str:
        cleaned = re.sub(r"\s+", "", str(value).strip())
        cleaned = re.sub(r"[：:，,。；;]+", "_", cleaned)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned[:max_length] or "综合"

    @staticmethod
    def _first_event_symbol(event: dict) -> str | None:
        for entity in event.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            entity_type = str(entity.get("entity_type") or "")
            ticker = str(entity.get("ticker") or "").strip()
            if ticker and entity_type in {"EQUITY", "INDEX", "COMMODITY"}:
                return ticker
        return None

    @staticmethod
    def _first_event_theme(event: dict) -> str | None:
        for entity in event.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            entity_type = str(entity.get("entity_type") or "")
            name = str(entity.get("name") or entity.get("ticker") or "").strip()
            if name and entity_type in {"THEME", "INDUSTRY", "MACRO"}:
                return name
        return None

    @staticmethod
    def _event_strategy_key(event: dict) -> str | None:
        event_type = str(event.get("event_type") or "").strip().upper()
        sentiment = str(event.get("sentiment") or "").strip().upper()
        if event_type == "RISK":
            return "viewpoint_risk"
        if event_type == "TRADING_ACTION":
            return "viewpoint_actionable"
        if sentiment == "BULLISH":
            return "viewpoint_bull"
        if sentiment == "BEARISH":
            return "viewpoint_bear"
        return None

    @staticmethod
    def _build_event_memory_payloads(metadata: dict, events: list[dict]) -> list[dict]:
        source_date = VideoIngestService._parse_source_datetime(metadata.get("publish_time"))
        valid_from = source_date or datetime.now(UTC)
        bvid = str(metadata.get("bvid") or metadata.get("platform_video_id") or "video")
        title = str(metadata.get("title") or "视频事件")
        payloads: list[dict] = []
        for event in events:
            statement = str(event.get("statement") or "").strip()
            if not statement:
                continue
            related_symbol = VideoIngestService._first_event_symbol(event)
            related_theme = VideoIngestService._first_event_theme(event)
            topic_label = related_symbol or related_theme or VideoIngestService._compact_label(statement)
            payloads.append(
                {
                    "memory_type": "media_event",
                    "title": f"{bvid}｜事件｜{event.get('event_type') or 'EVENT'}｜{topic_label}｜{int(event.get('event_index') or 0):02d}",
                    "content": "\n".join(
                        [
                            f"来源视频：{title}",
                            f"视频编号：{bvid}",
                            f"发布时间：{metadata.get('publish_time', '')}",
                            f"事件类型：{event.get('event_type')}",
                            f"主张类型：{event.get('claim_type')}",
                            f"情绪方向：{event.get('sentiment')}",
                            f"时间范围：{event.get('start_ms', 0)}-{event.get('end_ms', 0)} ms",
                            f"事件内容：{statement}",
                            f"条件：{event.get('condition_text') or '无'}",
                            f"证伪：{event.get('invalidation_text') or '无'}",
                            f"冲突状态：{event.get('conflict_status') or 'active'}",
                        ]
                    ),
                    "source_type": "bilibili_financial_event",
                    "source_date": source_date,
                    "valid_from": valid_from,
                    "related_theme": related_theme,
                    "related_symbol": related_symbol,
                    "related_strategy": f"event_{str(event.get('event_type') or 'unknown').lower()}",
                    "status": "validated",
                    "importance": "high" if event.get("conflict_status") != "superseded" else "medium",
                    "confidence": float(event.get("confidence_score") or 0.5),
                }
            )
        return payloads

    @staticmethod
    def _infer_video_type_from_events(events: list[dict]) -> str:
        event_types = {str(event.get("event_type") or "") for event in events}
        if {"PRICE_LEVEL", "TECHNICAL_TREND", "TECHNICAL_INDICATOR"} & event_types:
            return "EQUITY_TECHNICAL_ANALYSIS"
        if {"MACRO_INDICATOR"} & event_types:
            return "MACRO_ANALYSIS"
        if {"INDUSTRY_LOGIC"} & event_types:
            return "INDUSTRY_RESEARCH"
        return "GENERAL_FINANCE"

    @staticmethod
    def _parse_source_datetime(raw_value: str | None) -> datetime | None:
        if not raw_value:
            return None
        text = str(raw_value).strip()
        if len(text) != 8 or not text.isdigit():
            return None
        try:
            return datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None

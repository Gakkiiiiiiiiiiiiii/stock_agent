from __future__ import annotations

import hashlib
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
from engines.content.multimodal_context_builder import MultimodalContextBuilder
from engines.content.semantic_chunker import SemanticChunker
from engines.content.video_frame_extractor import VideoFrameExtractor
from engines.content.video_ocr_service import VideoOcrService
from engines.content.video_summary_exporter import VideoSummaryMarkdownExporter
from engines.content.transcript_postprocessor import TranscriptPostprocessor
from engines.content.video_vision_service import VideoVisionService
from engines.content.video_summarizer import VideoSummarizer
from engines.memory.memory_writer import enqueue_memory_reindex, write_memory_and_enqueue
from engines.retrieval.qdrant_client import FinancialQdrantClient
from financial_agent.utils import project_root
from storage.repositories.content_repository import ContentQueryRepository, ContentTaskRepository, FinancialEventRepository, VideoAssetRepository, VideoChunkRepository, VideoFrameRepository, VideoSummaryRepository
from storage.repositories.vector_repository import MemoryRepository, VectorMappingRepository


class VideoIngestService:
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
    ) -> None:
        self.bilibili_client = bilibili_client or BilibiliClient()
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
        self.video_repo = video_repo or VideoAssetRepository()
        self.chunk_repo = chunk_repo or VideoChunkRepository()
        self.event_repo = event_repo or FinancialEventRepository()
        self.frame_repo = frame_repo or VideoFrameRepository()
        self.summary_repo = summary_repo or VideoSummaryRepository()
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
            if detail and detail.get("summary"):
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

    def process_task(self, task_id: int) -> dict:
        task = self.task_repo.get(task_id)
        if task is None:
            raise FileNotFoundError(task_id)
        options = self.task_repo.serialize(task)["options"]
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
            self.task_repo.update(task_id, stage="chunk_and_extract", progress=76)
            chunks = self.semantic_chunker.build(transcript=transcript, frame_insights=frame_insights)
            chunks = self._enrich_chunks(chunks)
            self.chunk_repo.replace_for_video(asset.id, chunks)
            video_type, events = self.event_extractor.extract(metadata=metadata, chunks=chunks)
            events = self.conflict_resolver.resolve(events)
            self.event_repo.replace_for_video(asset.id, events, chunks=chunks)
            event_timeline = self.conflict_resolver.build_timeline(events)
            self.task_repo.update(task_id, stage="summarize", progress=82)
            summary_payload = self.summarizer.summarize(
                metadata=metadata,
                transcript=transcript,
                mode=options.get("summary_mode", "investment"),
                visual_context=visual_context,
                chunks=chunks,
                events=events,
                video_type=video_type,
            )
            summary = self.summary_repo.upsert(asset.id, summary_payload)
            export_path = self.summary_exporter.export(metadata=metadata, summary=summary_payload)
            index_result = None
            if options.get("index_to_memory", True):
                self.task_repo.update(task_id, stage="index_memory", progress=92)
                index_result = write_memory_and_enqueue(
                    self._build_memory_payload(metadata=metadata, summary=summary_payload, markdown_path=export_path),
                    target_collection="financial_knowledge",
                    existing_memory_id=summary.memory_record_id,
                )
                self.summary_repo.set_memory_record(summary.id, index_result["memory_id"])
                self._sync_viewpoint_memories(
                    metadata=metadata,
                    summary=summary_payload,
                    events=events,
                    target_collection="financial_knowledge",
                )
            self.task_repo.update(task_id, status="success", stage="success", progress=100, video_id=asset.id)
            detail = self.query_repo.get_video_detail(asset.id, summary_mode=options.get("summary_mode", "investment")) or {}
            return detail | {
                "task": self.task_repo.serialize(self.task_repo.get(task_id)),
                "index_result": index_result,
                "summary_export_path": str(export_path),
                "visual_context": visual_context,
                "video_type": video_type,
                "event_timeline": event_timeline,
            }
        except Exception as exc:
            if video_id is not None:
                self.video_repo.mark_transcript_failed(video_id)
            self.task_repo.mark_failed(task_id, str(exc), stage=self.task_repo.get(task_id).stage if self.task_repo.get(task_id) else "failed")
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
        return detail

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

    def get_video_frame_image_path(self, video_id: int, frame_index: int) -> str | None:
        payload = self.frame_repo.get_for_video_frame(video_id, frame_index)
        if payload is None:
            return None
        return payload.get("image_path")

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
            frames = self.frame_extractor.extract(
                video_path=video_path,
                output_dir=frame_output_dir,
                transcript_segments=transcript.get("segments") or [],
            )
            frame_insights = self.vision_service.analyze_frames(metadata=metadata, transcript=transcript, frames=frames)
            self.frame_repo.replace_for_video(video_id, frame_insights)
            if not frame_insights:
                return None
            return {
                "context": self.multimodal_context_builder.build(transcript=transcript, frame_insights=frame_insights),
                "frame_insights": frame_insights,
            }
        except Exception:
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
        auth_source = getattr(self.bilibili_client, "describe_auth_source", lambda: "anonymous")()
        raise RuntimeError(
            "Bilibili audio download looks incomplete. "
            f"Expected about {expected_duration:.0f}s but only fetched {audio_duration:.0f}s. "
            f"Current auth source: {auth_source}. "
            "For charged or member-only videos, run scripts/login-bilibili.ps1 to generate a project cookie file."
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

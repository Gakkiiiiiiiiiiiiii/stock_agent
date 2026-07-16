from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from engines.content.video_summary_exporter import VideoSummaryMarkdownExporter
from sqlalchemy import and_, delete, select

from storage.db import session_scope
from storage.models.content import ContentIngestTask, EventEvidence, FinancialEvent, VideoAsset, VideoChunk, VideoFrame, VideoSegment, VideoSummary


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None, default: object) -> object:
    if not value:
        return default
    return json.loads(value)


def _truncate_text(value: object, limit: int | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if limit is None or len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return f"{text[: limit - 1]}…"


class VideoAssetRepository:
    def get(self, video_id: int) -> VideoAsset | None:
        with session_scope() as session:
            return session.get(VideoAsset, video_id)

    def get_by_source(self, platform: str, platform_video_id: str | None = None, bvid: str | None = None) -> VideoAsset | None:
        with session_scope() as session:
            statement = select(VideoAsset).where(VideoAsset.platform == platform)
            if platform_video_id:
                statement = statement.where(VideoAsset.platform_video_id == platform_video_id)
            elif bvid:
                statement = statement.where(VideoAsset.bvid == bvid)
            else:
                return None
            return session.execute(statement).scalars().first()

    def upsert_metadata(self, metadata: dict) -> VideoAsset:
        with session_scope() as session:
            asset = session.execute(
                select(VideoAsset).where(
                    VideoAsset.platform == metadata["platform"],
                    VideoAsset.platform_video_id == metadata["platform_video_id"],
                )
            ).scalars().first()
            if asset is None:
                asset = VideoAsset(platform=metadata["platform"], platform_video_id=metadata["platform_video_id"])
            asset.bvid = metadata.get("bvid")
            asset.url = metadata["url"]
            asset.title = metadata["title"]
            asset.author_name = metadata.get("author_name")
            asset.author_id = metadata.get("author_id")
            asset.publish_time_raw = metadata.get("publish_time")
            asset.duration_seconds = metadata.get("duration_seconds")
            asset.cover_url = metadata.get("cover_url")
            asset.description = metadata.get("description")
            session.add(asset)
            session.flush()
            session.refresh(asset)
            return asset

    def update_audio(self, video_id: int, audio_path: str) -> None:
        with session_scope() as session:
            asset = session.get(VideoAsset, video_id)
            if asset is None:
                raise FileNotFoundError(video_id)
            asset.audio_path = audio_path
            asset.updated_at = datetime.now(UTC)
            session.add(asset)

    def save_transcript(self, video_id: int, transcript: dict) -> None:
        with session_scope() as session:
            asset = session.get(VideoAsset, video_id)
            if asset is None:
                raise FileNotFoundError(video_id)
            asset.transcript_text = transcript.get("text", "")
            asset.transcript_language = transcript.get("language")
            asset.transcript_status = "success"
            asset.asr_provider = transcript.get("provider")
            asset.asr_model = transcript.get("model")
            asset.source_hash = transcript.get("source_hash")
            asset.updated_at = datetime.now(UTC)
            session.add(asset)
            session.execute(delete(VideoSegment).where(VideoSegment.video_id == video_id))
            for segment in transcript.get("segments", []):
                session.add(
                    VideoSegment(
                        video_id=video_id,
                        segment_index=int(segment.get("segment_index", 0)),
                        start_ms=int(segment.get("start_ms", 0)),
                        end_ms=int(segment.get("end_ms", 0)),
                        speaker_label=segment.get("speaker_label"),
                        text=segment.get("text", ""),
                        avg_logprob=segment.get("avg_logprob"),
                        no_speech_prob=segment.get("no_speech_prob"),
                        compression_ratio=segment.get("compression_ratio"),
                        confidence_score=segment.get("confidence_score"),
                    )
                )

    def mark_transcript_failed(self, video_id: int) -> None:
        with session_scope() as session:
            asset = session.get(VideoAsset, video_id)
            if asset is None:
                return
            asset.transcript_status = "failed"
            asset.updated_at = datetime.now(UTC)
            session.add(asset)

    def list_segments(self, video_id: int) -> list[dict]:
        with session_scope() as session:
            rows = session.execute(select(VideoSegment).where(VideoSegment.video_id == video_id).order_by(VideoSegment.segment_index.asc())).scalars()
            return [
                {
                    "segment_index": row.segment_index,
                    "start_ms": row.start_ms,
                    "end_ms": row.end_ms,
                    "speaker_label": row.speaker_label,
                    "text": row.text,
                    "confidence_score": row.confidence_score,
                }
                for row in rows
            ]


class VideoChunkRepository:
    def replace_for_video(self, video_id: int, chunks: list[dict]) -> None:
        with session_scope() as session:
            session.execute(delete(VideoChunk).where(VideoChunk.video_id == video_id))
            for chunk in chunks:
                session.add(
                    VideoChunk(
                        video_id=video_id,
                        chunk_index=int(chunk.get("chunk_index") or 0),
                        start_ms=int(chunk.get("start_ms") or 0),
                        end_ms=int(chunk.get("end_ms") or 0),
                        topic=chunk.get("topic"),
                        transcript_text=chunk.get("transcript_text") or "",
                        ocr_text=chunk.get("ocr_text"),
                        visual_focus=chunk.get("visual_focus"),
                        entities_json=_dumps(chunk.get("entities") or []),
                        visual_tags_json=_dumps(chunk.get("visual_tags") or []),
                        confidence_score=chunk.get("confidence_score"),
                    )
                )

    def list_for_video(self, video_id: int) -> list[dict]:
        with session_scope() as session:
            rows = session.execute(
                select(VideoChunk).where(VideoChunk.video_id == video_id).order_by(VideoChunk.chunk_index.asc())
            ).scalars()
            return [
                {
                    "id": row.id,
                    "chunk_index": row.chunk_index,
                    "start_ms": row.start_ms,
                    "end_ms": row.end_ms,
                    "topic": row.topic,
                    "transcript_text": row.transcript_text,
                    "ocr_text": row.ocr_text,
                    "visual_focus": row.visual_focus,
                    "entities": _loads(row.entities_json, []),
                    "visual_tags": _loads(row.visual_tags_json, []),
                    "confidence_score": row.confidence_score,
                }
                for row in rows
            ]


class VideoSummaryRepository:
    def upsert(self, video_id: int, payload: dict) -> VideoSummary:
        mode = payload.get("summary_mode", "investment")
        with session_scope() as session:
            summary = session.execute(
                select(VideoSummary).where(VideoSummary.video_id == video_id, VideoSummary.summary_mode == mode)
            ).scalars().first()
            if summary is None:
                summary = VideoSummary(video_id=video_id, summary_mode=mode)
            summary.summary_markdown = payload.get("summary_markdown", "")
            summary.core_summary = payload.get("core_summary", "")
            summary.bull_points_json = _dumps(payload.get("bull_points", []))
            summary.bear_points_json = _dumps(payload.get("bear_points", []))
            summary.themes_json = _dumps(payload.get("themes", []))
            summary.symbols_json = _dumps(payload.get("symbols", []))
            summary.catalysts_json = _dumps(payload.get("catalysts", []))
            summary.risks_json = _dumps(payload.get("risks", []))
            summary.actionable_view = payload.get("actionable_view")
            summary.evidence_segments_json = _dumps(payload.get("evidence_segments", []))
            summary.confidence_score = payload.get("confidence_score")
            summary.llm_provider = payload.get("llm_provider")
            summary.llm_model = payload.get("llm_model")
            session.add(summary)
            session.flush()
            session.refresh(summary)
            return summary

    def set_memory_record(self, summary_id: int, memory_record_id: int) -> None:
        with session_scope() as session:
            summary = session.get(VideoSummary, summary_id)
            if summary is None:
                return
            summary.memory_record_id = memory_record_id
            session.add(summary)

    def get_for_video(self, video_id: int, mode: str = "investment") -> VideoSummary | None:
        with session_scope() as session:
            return session.execute(
                select(VideoSummary).where(VideoSummary.video_id == video_id, VideoSummary.summary_mode == mode)
            ).scalars().first()

    def delete_for_video(self, video_id: int, mode: str = "investment") -> bool:
        with session_scope() as session:
            summary = session.execute(
                select(VideoSummary).where(VideoSummary.video_id == video_id, VideoSummary.summary_mode == mode)
            ).scalars().first()
            if summary is None:
                return False
            session.delete(summary)
            return True

    def serialize(self, summary: VideoSummary | None) -> dict | None:
        if summary is None:
            return None
        return {
            "id": summary.id,
            "video_id": summary.video_id,
            "summary_mode": summary.summary_mode,
            "summary_markdown": summary.summary_markdown,
            "core_summary": summary.core_summary,
            "bull_points": _loads(summary.bull_points_json, []),
            "bear_points": _loads(summary.bear_points_json, []),
            "themes": _loads(summary.themes_json, []),
            "symbols": _loads(summary.symbols_json, []),
            "catalysts": _loads(summary.catalysts_json, []),
            "risks": _loads(summary.risks_json, []),
            "actionable_view": summary.actionable_view,
            "evidence_segments": _loads(summary.evidence_segments_json, []),
            "confidence_score": summary.confidence_score,
            "llm_provider": summary.llm_provider,
            "llm_model": summary.llm_model,
            "memory_record_id": summary.memory_record_id,
        }


class VideoFrameRepository:
    def replace_for_video(self, video_id: int, frames: list[dict]) -> None:
        with session_scope() as session:
            session.execute(delete(VideoFrame).where(VideoFrame.video_id == video_id))
            for frame in frames:
                session.add(
                    VideoFrame(
                        video_id=video_id,
                        frame_index=int(frame.get("frame_index") or 0),
                        timestamp_ms=int(frame.get("timestamp_ms") or 0),
                        image_path=str(frame.get("image_path") or ""),
                        trigger_source=frame.get("trigger_source"),
                        ocr_text=frame.get("ocr_text"),
                        visual_summary=frame.get("visual_summary"),
                        related_text=frame.get("related_text"),
                        themes_json=_dumps(frame.get("themes") or []),
                        symbols_json=_dumps(frame.get("symbols") or []),
                        confidence_score=frame.get("confidence_score"),
                    )
                )

    def list_for_video(self, video_id: int) -> list[dict]:
        with session_scope() as session:
            rows = session.execute(
                select(VideoFrame).where(VideoFrame.video_id == video_id).order_by(VideoFrame.timestamp_ms.asc())
            ).scalars()
            return [
                {
                    "frame_index": row.frame_index,
                    "timestamp_ms": row.timestamp_ms,
                    "image_path": row.image_path,
                    "trigger_source": row.trigger_source,
                    "ocr_text": row.ocr_text,
                    "visual_summary": row.visual_summary,
                    "related_text": row.related_text,
                    "themes": _loads(row.themes_json, []),
                    "symbols": _loads(row.symbols_json, []),
                    "confidence_score": row.confidence_score,
                }
                for row in rows
            ]

    def get_for_video_frame(self, video_id: int, frame_index: int) -> dict | None:
        with session_scope() as session:
            row = session.execute(
                select(VideoFrame).where(
                    VideoFrame.video_id == video_id,
                    VideoFrame.frame_index == frame_index,
                )
            ).scalars().first()
            if row is None:
                return None
            return {
                "frame_index": row.frame_index,
                "timestamp_ms": row.timestamp_ms,
                "image_path": row.image_path,
                "trigger_source": row.trigger_source,
                "ocr_text": row.ocr_text,
                "visual_summary": row.visual_summary,
                "related_text": row.related_text,
                "themes": _loads(row.themes_json, []),
                "symbols": _loads(row.symbols_json, []),
                "confidence_score": row.confidence_score,
            }


class FinancialEventRepository:
    def delete_for_video(self, video_id: int) -> None:
        with session_scope() as session:
            existing_events = session.execute(
                select(FinancialEvent.id).where(FinancialEvent.video_id == video_id)
            ).scalars().all()
            if existing_events:
                session.execute(delete(EventEvidence).where(EventEvidence.event_id.in_(existing_events)))
            session.execute(delete(FinancialEvent).where(FinancialEvent.video_id == video_id))

    def replace_for_video(self, video_id: int, events: list[dict], chunks: list[dict] | None = None) -> None:
        chunk_index_to_id: dict[int, int] = {}
        with session_scope() as session:
            existing_chunks = session.execute(
                select(VideoChunk).where(VideoChunk.video_id == video_id)
            ).scalars()
            for row in existing_chunks:
                chunk_index_to_id[row.chunk_index] = row.id
            existing_events = session.execute(
                select(FinancialEvent.id).where(FinancialEvent.video_id == video_id)
            ).scalars().all()
            if existing_events:
                session.execute(delete(EventEvidence).where(EventEvidence.event_id.in_(existing_events)))
            session.execute(delete(FinancialEvent).where(FinancialEvent.video_id == video_id))
            for index, event in enumerate(events, start=1):
                chunk_index = event.get("chunk_index")
                chunk_id = chunk_index_to_id.get(int(chunk_index)) if chunk_index is not None else None
                row = FinancialEvent(
                    video_id=video_id,
                    chunk_id=chunk_id,
                    event_index=int(event.get("event_index") or index),
                    event_type=_truncate_text(event.get("event_type") or "OPINION", 64) or "OPINION",
                    claim_type=_truncate_text(event.get("claim_type"), 32),
                    sentiment=_truncate_text(event.get("sentiment"), 32),
                    subjectivity=_truncate_text(event.get("subjectivity"), 32),
                    certainty=event.get("certainty"),
                    confidence_score=event.get("confidence_score"),
                    statement=event.get("statement") or "",
                    time_expression=_truncate_text(event.get("time_expression"), 255),
                    normalized_time_start=_truncate_text(event.get("normalized_time_start"), 64),
                    normalized_time_end=_truncate_text(event.get("normalized_time_end"), 64),
                    start_ms=event.get("start_ms"),
                    end_ms=event.get("end_ms"),
                    condition_text=event.get("condition_text"),
                    invalidation_text=event.get("invalidation_text"),
                    entities_json=_dumps(event.get("entities") or []),
                    attributes_json=_dumps(event.get("attributes") or {}),
                    conflict_key=_truncate_text(event.get("conflict_key"), 256),
                    conflict_status=_truncate_text(event.get("conflict_status"), 32),
                    superseded_by_event_id=event.get("superseded_by_event_id"),
                )
                session.add(row)
                session.flush()
                for evidence in event.get("evidence") or []:
                    session.add(
                        EventEvidence(
                            event_id=row.id,
                            source_type=_truncate_text(evidence.get("source_type") or "ASR", 32) or "ASR",
                            source_id=_truncate_text(evidence.get("source_id"), 128),
                            evidence_text=evidence.get("text") or evidence.get("evidence_text") or "",
                            timestamp_ms=evidence.get("timestamp_ms") or evidence.get("start_ms"),
                            confidence_score=evidence.get("confidence_score") or evidence.get("confidence"),
                            image_path=evidence.get("image_path"),
                        )
                    )

    def list_for_video(self, video_id: int) -> list[dict]:
        with session_scope() as session:
            rows = list(
                session.execute(
                    select(FinancialEvent).where(FinancialEvent.video_id == video_id).order_by(FinancialEvent.start_ms.asc(), FinancialEvent.id.asc())
                ).scalars()
            )
            if not rows:
                return []
            event_ids = [row.id for row in rows]
            evidence_rows = list(
                session.execute(
                    select(EventEvidence).where(EventEvidence.event_id.in_(event_ids)).order_by(EventEvidence.id.asc())
                ).scalars()
            )
            evidence_by_event: dict[int, list[dict]] = {}
            for evidence in evidence_rows:
                evidence_by_event.setdefault(evidence.event_id, []).append(
                    {
                        "id": evidence.id,
                        "source_type": evidence.source_type,
                        "source_id": evidence.source_id,
                        "text": evidence.evidence_text,
                        "timestamp_ms": evidence.timestamp_ms,
                        "confidence_score": evidence.confidence_score,
                        "image_path": evidence.image_path,
                    }
                )
            return [self._serialize(row, evidence_by_event.get(row.id, [])) for row in rows]

    @staticmethod
    def _serialize(row: FinancialEvent, evidence: list[dict]) -> dict:
        return {
            "id": row.id,
            "video_id": row.video_id,
            "chunk_id": row.chunk_id,
            "event_index": row.event_index,
            "event_type": row.event_type,
            "claim_type": row.claim_type,
            "sentiment": row.sentiment,
            "subjectivity": row.subjectivity,
            "certainty": row.certainty,
            "confidence_score": row.confidence_score,
            "statement": row.statement,
            "time_expression": row.time_expression,
            "normalized_time_start": row.normalized_time_start,
            "normalized_time_end": row.normalized_time_end,
            "start_ms": row.start_ms,
            "end_ms": row.end_ms,
            "condition_text": row.condition_text,
            "invalidation_text": row.invalidation_text,
            "entities": _loads(row.entities_json, []),
            "attributes": _loads(row.attributes_json, {}),
            "conflict_key": row.conflict_key,
            "conflict_status": row.conflict_status,
            "superseded_by_event_id": row.superseded_by_event_id,
            "evidence": evidence,
        }


class ContentTaskRepository:
    def create(self, source_type: str, source_ref: str, options: dict, video_id: int | None = None) -> ContentIngestTask:
        with session_scope() as session:
            task = ContentIngestTask(
                source_type=source_type,
                source_ref=source_ref,
                video_id=video_id,
                options_json=_dumps(options),
                status="pending",
                stage="queued",
                progress=0,
            )
            session.add(task)
            session.flush()
            session.refresh(task)
            return task

    def get(self, task_id: int) -> ContentIngestTask | None:
        with session_scope() as session:
            return session.get(ContentIngestTask, task_id)

    def next_pending(self) -> ContentIngestTask | None:
        with session_scope() as session:
            task = session.execute(
                select(ContentIngestTask).where(ContentIngestTask.status == "pending").order_by(ContentIngestTask.created_at.asc())
            ).scalars().first()
            if task:
                task.status = "processing"
                task.stage = "starting"
                task.updated_at = datetime.now(UTC)
                session.add(task)
                session.flush()
                session.refresh(task)
            return task

    def update(self, task_id: int, *, status: str | None = None, stage: str | None = None, progress: int | None = None, error_message: str | None = None, video_id: int | None = None) -> None:
        with session_scope() as session:
            task = session.get(ContentIngestTask, task_id)
            if task is None:
                return
            if status is not None:
                task.status = status
            if stage is not None:
                task.stage = stage
            if progress is not None:
                task.progress = progress
            if error_message is not None:
                task.error_message = error_message
            if video_id is not None:
                task.video_id = video_id
            task.updated_at = datetime.now(UTC)
            session.add(task)

    def mark_failed(self, task_id: int, error_message: str, stage: str) -> None:
        with session_scope() as session:
            task = session.get(ContentIngestTask, task_id)
            if task is None:
                return
            task.status = "failed"
            task.stage = stage
            task.error_message = error_message
            task.retry_count += 1
            task.updated_at = datetime.now(UTC)
            session.add(task)

    def serialize(self, task: ContentIngestTask | None) -> dict | None:
        if task is None:
            return None
        return {
            "task_id": task.id,
            "video_id": task.video_id,
            "status": task.status,
            "stage": task.stage,
            "progress": task.progress,
            "error_message": task.error_message,
            "options": _loads(task.options_json, {}),
        }


class ContentQueryRepository:
    def __init__(self) -> None:
        self.video_repo = VideoAssetRepository()
        self.chunk_repo = VideoChunkRepository()
        self.event_repo = FinancialEventRepository()
        self.summary_repo = VideoSummaryRepository()
        self.frame_repo = VideoFrameRepository()
        self.summary_exporter = VideoSummaryMarkdownExporter()

    def get_video_detail(self, video_id: int, summary_mode: str = "investment") -> dict | None:
        asset = self.video_repo.get(video_id)
        if asset is None:
            return None
        summary = self.summary_repo.get_for_video(video_id, mode=summary_mode)
        video_payload = {
            "id": asset.id,
            "platform": asset.platform,
            "platform_video_id": asset.platform_video_id,
            "bvid": asset.bvid,
            "url": asset.url,
            "title": asset.title,
            "author_name": asset.author_name,
            "author_id": asset.author_id,
            "publish_time": asset.publish_time_raw,
            "duration_seconds": asset.duration_seconds,
            "cover_url": asset.cover_url,
            "description": asset.description,
            "audio_path": asset.audio_path,
            "transcript_text": asset.transcript_text,
            "transcript_language": asset.transcript_language,
            "transcript_status": asset.transcript_status,
            "asr_provider": asset.asr_provider,
            "asr_model": asset.asr_model,
        }
        export_path = self.summary_exporter.resolve_existing_path(video_payload)
        return {
            "video": video_payload,
            "summary": self.summary_repo.serialize(summary),
            "segments": self.video_repo.list_segments(video_id),
            "chunks": self.chunk_repo.list_for_video(video_id),
            "events": self.event_repo.list_for_video(video_id),
            "visual_frames": self.frame_repo.list_for_video(video_id),
            "summary_export_path": str(export_path) if export_path else None,
        }

    def list_videos(self, summary_mode: str = "investment", limit: int = 50) -> list[dict]:
        with session_scope() as session:
            rows = session.execute(
                select(VideoAsset, VideoSummary)
                .join(
                    VideoSummary,
                    and_(VideoSummary.video_id == VideoAsset.id, VideoSummary.summary_mode == summary_mode),
                    isouter=True,
                )
                .where(VideoAsset.platform == "bilibili")
                .order_by(VideoAsset.publish_time_raw.desc(), VideoAsset.updated_at.desc())
                .limit(limit)
            ).all()

        items: list[dict] = []
        seen_paths: set[str] = set()
        for asset, summary in rows:
            if summary is None:
                continue
            video_payload = {
                "id": asset.id,
                "platform": asset.platform,
                "platform_video_id": asset.platform_video_id,
                "bvid": asset.bvid,
                "url": asset.url,
                "title": asset.title,
                "author_name": asset.author_name,
                "publish_time": asset.publish_time_raw,
                "duration_seconds": asset.duration_seconds,
                "transcript_status": asset.transcript_status,
                "asr_model": asset.asr_model,
                "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
            }
            export_path = self.summary_exporter.resolve_existing_path(video_payload)
            export_path_str = str(export_path) if export_path else None
            if export_path_str:
                seen_paths.add(str(Path(export_path_str).resolve()).lower())
            items.append(
                {
                    "video_id": asset.id,
                    "title": asset.title,
                    "bvid": asset.bvid,
                    "author_name": asset.author_name,
                    "publish_time": asset.publish_time_raw,
                    "duration_seconds": asset.duration_seconds,
                    "transcript_status": asset.transcript_status,
                    "asr_model": asset.asr_model,
                    "summary_ready": summary is not None,
                    "summary_mode": summary_mode,
                    "summary_model": summary.llm_model if summary else None,
                    "summary_confidence": summary.confidence_score if summary else None,
                    "summary_export_path": export_path_str,
                    "summary_doc_path": self._relative_summary_path(export_path) if export_path else None,
                    "summary_source": "database",
                    "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
                }
            )
        for item in self._list_markdown_only_summaries(limit=limit):
            export_path = item.get("summary_export_path")
            if export_path and str(Path(export_path).resolve()).lower() in seen_paths:
                continue
            items.append(item)
        items.sort(
            key=lambda item: (
                str(item.get("publish_time") or ""),
                str(item.get("updated_at") or ""),
                str(item.get("title") or ""),
            ),
            reverse=True,
        )
        return items[:limit]

    def get_video_summary_document(self, video_id: int, summary_mode: str = "investment") -> dict | None:
        detail = self.get_video_detail(video_id, summary_mode=summary_mode)
        if detail is None:
            return None

        summary = detail.get("summary") or {}
        export_path = detail.get("summary_export_path")
        content = None
        source = None
        if export_path:
            path = Path(export_path)
            if path.exists():
                content = path.read_text(encoding="utf-8")
                source = "markdown_export"
        if content is None:
            content = summary.get("summary_markdown")
            if content:
                source = "database_summary"
        if not content:
            return None
        return {
            "video_id": video_id,
            "summary_mode": summary_mode,
            "title": detail["video"].get("title"),
            "path": export_path,
            "content": content,
            "source": source,
        }

    def find_video_by_summary_path(self, summary_path: str, summary_mode: str = "investment") -> dict | None:
        normalized_input = Path(summary_path).as_posix().lower()
        items = self.list_videos(summary_mode=summary_mode, limit=500)
        for item in items:
            candidates = self._summary_path_candidates(item.get("summary_export_path"), item.get("summary_doc_path"))
            if normalized_input in candidates:
                return item
        return None

    def _list_markdown_only_summaries(self, limit: int = 50) -> list[dict]:
        export_root = self.summary_exporter.export_root
        if not export_root.exists():
            return []
        items: list[dict] = []
        for path in sorted(export_root.glob("*.md"), key=lambda candidate: candidate.stat().st_mtime, reverse=True):
            content = path.read_text(encoding="utf-8")
            title = self._extract_markdown_title(path, content)
            meta = self._parse_summary_filename(path)
            updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
            items.append(
                {
                    "video_id": None,
                    "title": title,
                    "bvid": meta.get("bvid"),
                    "author_name": self._extract_markdown_meta(content, "作者"),
                    "publish_time": meta.get("publish_time") or self._extract_markdown_meta(content, "发布时间"),
                    "duration_seconds": None,
                    "transcript_status": None,
                    "asr_model": None,
                    "summary_ready": True,
                    "summary_mode": "investment",
                    "summary_model": self._extract_summary_model(content),
                    "summary_confidence": self._extract_markdown_meta(content, "置信度"),
                    "summary_export_path": str(path.resolve()),
                    "summary_doc_path": self._relative_summary_path(path),
                    "summary_source": "markdown_only",
                    "updated_at": updated_at,
                }
            )
            if len(items) >= limit:
                break
        return items

    def _relative_summary_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.summary_exporter.export_root.parent).as_posix()

    @staticmethod
    def _extract_markdown_title(path: Path, content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return path.stem

    @staticmethod
    def _extract_markdown_meta(content: str, label: str) -> str | None:
        prefix = f"- {label}："
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(prefix):
                value = stripped[len(prefix):].strip()
                return value or None
        return None

    @staticmethod
    def _extract_summary_model(content: str) -> str | None:
        value = ContentQueryRepository._extract_markdown_meta(content, "总结模型")
        if not value:
            return None
        parts = [part.strip() for part in value.split("/") if part.strip()]
        return parts[-1] if parts else value

    @staticmethod
    def _parse_summary_filename(path: Path) -> dict:
        match = re.match(r"^(?P<publish>\d{8})_(?P<bvid>BV[0-9A-Za-z]+)_.+\.md$", path.name, flags=re.IGNORECASE)
        if not match:
            return {}
        return {
            "publish_time": match.group("publish"),
            "bvid": match.group("bvid"),
        }

    def _summary_path_candidates(self, export_path: str | None, doc_path: str | None) -> set[str]:
        candidates: set[str] = set()
        if doc_path:
            candidates.add(Path(doc_path).as_posix().lower())
        if export_path:
            resolved = Path(export_path)
            candidates.add(resolved.as_posix().lower())
            candidates.add(resolved.name.lower())
            try:
                candidates.add(resolved.relative_to(self.summary_exporter.export_root).as_posix().lower())
            except ValueError:
                pass
            try:
                candidates.add(resolved.relative_to(self.summary_exporter.export_root.parent).as_posix().lower())
            except ValueError:
                pass
        return candidates

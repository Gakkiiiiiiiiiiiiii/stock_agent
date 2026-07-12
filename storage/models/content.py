from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


class VideoAsset(Base):
    __tablename__ = "video_asset"
    __table_args__ = (UniqueConstraint("platform", "platform_video_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), default="bilibili")
    platform_video_id: Mapped[str] = mapped_column(String(128))
    bvid: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(512))
    author_name: Mapped[str | None] = mapped_column(String(256))
    author_id: Mapped[str | None] = mapped_column(String(128))
    publish_time_raw: Mapped[str | None] = mapped_column(String(32))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    cover_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    audio_path: Mapped[str | None] = mapped_column(Text)
    transcript_text: Mapped[str | None] = mapped_column(Text)
    transcript_language: Mapped[str | None] = mapped_column(String(32))
    transcript_status: Mapped[str] = mapped_column(String(32), default="pending")
    asr_provider: Mapped[str | None] = mapped_column(String(64))
    asr_model: Mapped[str | None] = mapped_column(String(128))
    source_hash: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class VideoSegment(Base):
    __tablename__ = "video_segment"
    __table_args__ = (UniqueConstraint("video_id", "segment_index"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_asset.id"), index=True)
    segment_index: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    speaker_label: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    avg_logprob: Mapped[float | None] = mapped_column(Float)
    no_speech_prob: Mapped[float | None] = mapped_column(Float)
    compression_ratio: Mapped[float | None] = mapped_column(Float)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class VideoChunk(Base):
    __tablename__ = "video_chunk"
    __table_args__ = (UniqueConstraint("video_id", "chunk_index"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_asset.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    topic: Mapped[str | None] = mapped_column(String(512))
    transcript_text: Mapped[str] = mapped_column(Text)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    visual_focus: Mapped[str | None] = mapped_column(Text)
    entities_json: Mapped[str] = mapped_column(Text, default="[]")
    visual_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence_score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class FinancialEvent(Base):
    __tablename__ = "financial_event"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_asset.id"), index=True)
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("video_chunk.id"), index=True)
    event_index: Mapped[int] = mapped_column(Integer, default=0)
    event_type: Mapped[str] = mapped_column(String(64))
    claim_type: Mapped[str | None] = mapped_column(String(32))
    sentiment: Mapped[str | None] = mapped_column(String(32))
    subjectivity: Mapped[str | None] = mapped_column(String(32))
    certainty: Mapped[float | None] = mapped_column(Float)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    statement: Mapped[str] = mapped_column(Text)
    time_expression: Mapped[str | None] = mapped_column(String(255))
    normalized_time_start: Mapped[str | None] = mapped_column(String(64))
    normalized_time_end: Mapped[str | None] = mapped_column(String(64))
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    condition_text: Mapped[str | None] = mapped_column(Text)
    invalidation_text: Mapped[str | None] = mapped_column(Text)
    entities_json: Mapped[str] = mapped_column(Text, default="[]")
    attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    conflict_key: Mapped[str | None] = mapped_column(String(256))
    conflict_status: Mapped[str | None] = mapped_column(String(32))
    superseded_by_event_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class EventEvidence(Base):
    __tablename__ = "event_evidence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("financial_event.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(128))
    evidence_text: Mapped[str] = mapped_column(Text)
    timestamp_ms: Mapped[int | None] = mapped_column(Integer)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    image_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class VideoSummary(Base):
    __tablename__ = "video_summary"
    __table_args__ = (UniqueConstraint("video_id", "summary_mode"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_asset.id"), index=True)
    summary_mode: Mapped[str] = mapped_column(String(64), default="investment")
    summary_markdown: Mapped[str] = mapped_column(Text)
    core_summary: Mapped[str] = mapped_column(Text)
    bull_points_json: Mapped[str] = mapped_column(Text, default="[]")
    bear_points_json: Mapped[str] = mapped_column(Text, default="[]")
    themes_json: Mapped[str] = mapped_column(Text, default="[]")
    symbols_json: Mapped[str] = mapped_column(Text, default="[]")
    catalysts_json: Mapped[str] = mapped_column(Text, default="[]")
    risks_json: Mapped[str] = mapped_column(Text, default="[]")
    actionable_view: Mapped[str | None] = mapped_column(Text)
    evidence_segments_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence_score: Mapped[float | None] = mapped_column(Float)
    llm_provider: Mapped[str | None] = mapped_column(String(64))
    llm_model: Mapped[str | None] = mapped_column(String(128))
    memory_record_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class VideoFrame(Base):
    __tablename__ = "video_frame"
    __table_args__ = (UniqueConstraint("video_id", "frame_index"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_asset.id"), index=True)
    frame_index: Mapped[int] = mapped_column(Integer)
    timestamp_ms: Mapped[int] = mapped_column(Integer)
    image_path: Mapped[str] = mapped_column(Text)
    trigger_source: Mapped[str | None] = mapped_column(String(32))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    visual_summary: Mapped[str | None] = mapped_column(Text)
    related_text: Mapped[str | None] = mapped_column(Text)
    themes_json: Mapped[str] = mapped_column(Text, default="[]")
    symbols_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence_score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class ContentIngestTask(Base):
    __tablename__ = "content_ingest_task"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(64), default="bilibili")
    source_ref: Mapped[str] = mapped_column(Text)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("video_asset.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

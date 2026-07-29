from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


class VideoChapter(Base):
    __tablename__ = "video_chapter"
    __table_args__ = (UniqueConstraint("video_id", "chapter_index"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_asset.id"), index=True)
    chapter_index: Mapped[int] = mapped_column(Integer)
    parent_chapter_id: Mapped[int | None] = mapped_column(ForeignKey("video_chapter.id"))
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(512))
    chapter_type: Mapped[str] = mapped_column(String(64), index=True)
    primary_domain: Mapped[str] = mapped_column(String(32), index=True)
    secondary_domains_json: Mapped[str] = mapped_column(Text, default="[]")
    summary: Mapped[str | None] = mapped_column(Text)
    entities_json: Mapped[str] = mapped_column(Text, default="[]")
    boundary_source: Mapped[str | None] = mapped_column(String(32))
    boundary_score: Mapped[float | None] = mapped_column(Float)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    content_hash: Mapped[str] = mapped_column(String(128), index=True)
    parser_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class KnowledgeUnit(Base):
    __tablename__ = "knowledge_unit"
    __table_args__ = (UniqueConstraint("source_video_id", "source_chapter_id", "content_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    knowledge_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_video_id: Mapped[int] = mapped_column(ForeignKey("video_asset.id"), index=True)
    source_chapter_id: Mapped[int] = mapped_column(ForeignKey("video_chapter.id"), index=True)
    primary_domain: Mapped[str] = mapped_column(String(32), index=True)
    secondary_domains_json: Mapped[str] = mapped_column(Text, default="[]")
    knowledge_kind: Mapped[str] = mapped_column(String(32), index=True)
    temporal_class: Mapped[str] = mapped_column(String(32), index=True)
    expression_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_type: Mapped[str | None] = mapped_column(String(32), index=True)
    subject_key: Mapped[str | None] = mapped_column(String(128), index=True)
    subject_name: Mapped[str | None] = mapped_column(String(256))
    predicate_key: Mapped[str | None] = mapped_column(String(128), index=True)
    statement: Mapped[str] = mapped_column(Text)
    canonical_statement: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str | None] = mapped_column(String(32))
    sentiment: Mapped[str | None] = mapped_column(String(32))
    certainty_score: Mapped[float | None] = mapped_column(Float)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    as_of_time: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    time_horizon: Mapped[str | None] = mapped_column(String(32), index=True)
    timeframe: Mapped[str | None] = mapped_column(String(32), index=True)
    decay_half_life_days: Mapped[float | None] = mapped_column(Float)
    condition_text: Mapped[str | None] = mapped_column(Text)
    invalidation_text: Mapped[str | None] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="EXTRACTED", index=True)
    verification_status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED", index=True)
    scope_type: Mapped[str | None] = mapped_column(String(32), index=True)
    scope_key: Mapped[str | None] = mapped_column(String(128), index=True)
    conflict_key: Mapped[str | None] = mapped_column(String(256), index=True)
    conflict_group_id: Mapped[str | None] = mapped_column(String(64), index=True)
    superseded_by_unit_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_unit.id"))
    content_hash: Mapped[str] = mapped_column(String(128), index=True)
    semantic_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    extractor_provider: Mapped[str | None] = mapped_column(String(64))
    extractor_model: Mapped[str | None] = mapped_column(String(128))
    extractor_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class KnowledgeEvidence(Base):
    __tablename__ = "knowledge_evidence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    knowledge_unit_id: Mapped[int] = mapped_column(ForeignKey("knowledge_unit.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(128))
    evidence_text: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    frame_id: Mapped[int | None] = mapped_column(ForeignKey("video_frame.id"))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class KnowledgeEntityRelation(Base):
    __tablename__ = "knowledge_entity_relation"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    knowledge_unit_id: Mapped[int] = mapped_column(ForeignKey("knowledge_unit.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_key: Mapped[str | None] = mapped_column(String(128), index=True)
    entity_name: Mapped[str] = mapped_column(String(256))
    ticker: Mapped[str | None] = mapped_column(String(32), index=True)
    relation_role: Mapped[str] = mapped_column(String(32), index=True)
    confidence_score: Mapped[float | None] = mapped_column(Float)


class KnowledgeUnitRelation(Base):
    __tablename__ = "knowledge_unit_relation"
    __table_args__ = (UniqueConstraint("source_unit_id", "target_unit_id", "relation_type"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_unit_id: Mapped[int] = mapped_column(ForeignKey("knowledge_unit.id"), index=True)
    target_unit_id: Mapped[int] = mapped_column(ForeignKey("knowledge_unit.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(32), index=True)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    attributes_json: Mapped[str] = mapped_column(Text, default="{}")


class VideoAnalysisDocument(Base):
    __tablename__ = "video_analysis_document"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_asset.id"), unique=True, index=True)
    document_markdown: Mapped[str] = mapped_column(Text)
    core_summary: Mapped[str] = mapped_column(Text)
    video_type: Mapped[str] = mapped_column(String(64))
    primary_domains_json: Mapped[str] = mapped_column(Text, default="[]")
    chapter_count: Mapped[int] = mapped_column(Integer)
    knowledge_unit_count: Mapped[int] = mapped_column(Integer)
    method_count: Mapped[int] = mapped_column(Integer, default=0)
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    state_count: Mapped[int] = mapped_column(Integer, default=0)
    thesis_count: Mapped[int] = mapped_column(Integer, default=0)
    forecast_count: Mapped[int] = mapped_column(Integer, default=0)
    action_count: Mapped[int] = mapped_column(Integer, default=0)
    risk_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    generator_provider: Mapped[str | None] = mapped_column(String(64))
    generator_model: Mapped[str | None] = mapped_column(String(128))
    generator_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class KnowledgeExtractionRun(Base):
    __tablename__ = "knowledge_extraction_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_asset.id"), index=True)
    source_hash: Mapped[str] = mapped_column(String(128), index=True)
    parser_version: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str | None] = mapped_column(String(64))
    chapter_count: Mapped[int] = mapped_column(Integer, default=0)
    knowledge_unit_count: Mapped[int] = mapped_column(Integer, default=0)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

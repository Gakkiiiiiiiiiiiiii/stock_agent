from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


class VectorIndexTask(Base):
    __tablename__ = "vector_index_task"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(32), default="upsert")
    postgres_table: Mapped[str] = mapped_column(String(128))
    postgres_id: Mapped[int] = mapped_column(Integer)
    target_collection: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class VectorIndexMapping(Base):
    __tablename__ = "vector_index_mapping"
    __table_args__ = (UniqueConstraint("postgres_table", "postgres_id", "chunk_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    postgres_table: Mapped[str] = mapped_column(String(128))
    postgres_id: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str] = mapped_column(String(256))
    qdrant_collection: Mapped[str] = mapped_column(String(128))
    qdrant_point_id: Mapped[str] = mapped_column(String(128))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedding_version: Mapped[str | None] = mapped_column(String(64))
    sparse_model: Mapped[str | None] = mapped_column(String(128))
    reranker_model: Mapped[str | None] = mapped_column(String(128))
    content_hash: Mapped[str | None] = mapped_column(String(128))
    index_status: Mapped[str] = mapped_column(String(32), default="pending")
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class MemoryRecord(Base):
    __tablename__ = "memory_record"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    memory_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64))
    source_date: Mapped[datetime | None] = mapped_column(DateTime)
    related_regime: Mapped[str | None] = mapped_column(String(64))
    related_strategy: Mapped[str | None] = mapped_column(String(64))
    related_theme: Mapped[str | None] = mapped_column(String(128))
    related_symbol: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="validated")
    importance: Mapped[str] = mapped_column(String(32), default="medium")
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class MarketRegimeLabel(Base):
    __tablename__ = "market_regime_label"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date)
    universe: Mapped[str] = mapped_column(String(64))
    decision_mode: Mapped[str | None] = mapped_column(String(32))
    label_type: Mapped[str | None] = mapped_column(String(32))
    primary_regime: Mapped[str | None] = mapped_column(String(64))
    secondary_regime: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)
    label_source: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


class MarketFeatureSnapshot(Base):
    __tablename__ = "market_feature_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market_code: Mapped[str] = mapped_column(String(32), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    feature_version: Mapped[str] = mapped_column(String(64))
    features_json: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_score: Mapped[float | None] = mapped_column(Float)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("market_code", "trade_date", "feature_version", name="uq_market_feature_snapshot_key"),
    )


class SectorFeatureSnapshot(Base):
    __tablename__ = "sector_feature_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sector_code: Mapped[str | None] = mapped_column(String(32), index=True)
    sector_name: Mapped[str] = mapped_column(String(128), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime)
    component_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    final_score: Mapped[float] = mapped_column(Float)
    universe_size: Mapped[int] = mapped_column(Integer, default=0)
    valid_symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    feature_version: Mapped[str] = mapped_column(String(64))
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("sector_name", "trade_date", "feature_version", name="uq_sector_feature_snapshot_key"),
    )


class SymbolSectorMembership(Base):
    __tablename__ = "symbol_sector_membership"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    sector_code: Mapped[str] = mapped_column(String(32), index=True)
    sector_name: Mapped[str] = mapped_column(String(128))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_symbol_sector_membership_symbol_valid_from", "symbol", "valid_from"),
    )

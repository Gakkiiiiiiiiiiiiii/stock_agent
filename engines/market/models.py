from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class FeatureValue(BaseModel):
    value: float | None = None
    as_of: datetime | None = None
    source: str | None = None
    coverage: float | None = None
    quality_flags: list[str] = Field(default_factory=list)
    calculation_version: str | None = None


class SectorComponents(BaseModel):
    trend: float | None = None
    breadth: float | None = None
    relative_strength: float | None = None
    liquidity: float | None = None
    momentum: float | None = None
    risk_penalty: float | None = None


class SectorStrengthResult(BaseModel):
    sector: str
    sector_code: str | None = None
    strength_score: float | None = None
    rank: int | None = None
    universe_size: int = 0
    valid_symbol_count: int = 0
    coverage: float | None = None
    components: SectorComponents = Field(default_factory=SectorComponents)
    as_of: datetime | None = None
    feature_version: str | None = None
    quality_flags: list[str] = Field(default_factory=list)


class MarketFeatureSnapshotModel(BaseModel):
    market_code: str | None = None
    trade_date: date | None = None
    as_of: datetime | None = None
    feature_version: str | None = None
    features: dict[str, Any] = Field(default_factory=dict)
    quality_score: float | None = None
    quality_flags: list[str] = Field(default_factory=list)

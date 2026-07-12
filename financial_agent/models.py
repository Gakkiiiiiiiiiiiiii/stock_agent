from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class KlineRecord(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    turnover_rate: float | None = None


class KlineResponse(BaseModel):
    symbol: str
    freq: str = "1d"
    adjust: str = "qfq"
    records: list[KlineRecord]


class SignalResult(BaseModel):
    pattern: str
    triggered: bool
    score: int = Field(ge=0, le=100)
    entry_type: str | None = None
    evidence: list[str] = Field(default_factory=list)
    risk: list[str] = Field(default_factory=list)
    confirm_condition: str | None = None
    stop_condition: str | None = None


class ThemeStock(BaseModel):
    symbol: str
    name: str = ""
    relation: str = ""
    sensitivity_score: float = Field(default=50, ge=0, le=100)
    certainty_score: float = Field(default=50, ge=0, le=100)


class ThemeLogic(BaseModel):
    theme_name: str
    aliases: list[str] = Field(default_factory=list)
    core_thesis: str = ""
    industry_chain: list[str] = Field(default_factory=list)
    catalysts: list[str] = Field(default_factory=list)
    monitor_keywords: list[str] = Field(default_factory=list)
    trigger_rules: list[str] = Field(default_factory=list)
    invalidation_rules: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    related_stocks: list[ThemeStock] = Field(default_factory=list)


class ThemeScoreInput(BaseModel):
    theme: str
    price_strength_score: float = 50
    volume_score: float = 50
    fund_flow_score: float = 50
    news_score: float = 50
    technical_score: float = 50
    knowledge_score: float = 50
    risk_score: float = 0


class ThemeScoreResult(BaseModel):
    theme: str
    score: float
    components: dict[str, float]
    reason: str


class Position(BaseModel):
    symbol: str
    name: str = ""
    theme: str | None = None
    market_value: float
    cost: float | None = None
    latest_price: float | None = None


class RiskReview(BaseModel):
    total_market_value: float
    concentration: list[dict[str, Any]]
    theme_exposure: list[dict[str, Any]]
    warnings: list[str]
    suggested_position: str


class TradeReviewInput(BaseModel):
    trade_date: date
    symbol: str
    action: Literal["buy", "sell", "hold", "trim", "add"]
    reason: str = ""
    matched_strategy: str | None = None
    trade_price: float | None = None
    trade_qty: float | None = None

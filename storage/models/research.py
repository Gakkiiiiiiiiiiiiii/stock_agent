from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


class MarketRegimeState(Base):
    __tablename__ = "market_regime_state"

    market_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    confirmed_regime: Mapped[str] = mapped_column(String(64))
    confirmed_since: Mapped[date] = mapped_column(Date)
    candidate_regime: Mapped[str | None] = mapped_column(String(64))
    candidate_since: Mapped[date | None] = mapped_column(Date)
    candidate_days: Mapped[int] = mapped_column(Integer, default=0)
    last_evaluated_date: Mapped[date | None] = mapped_column(Date)
    confirmed_days: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float | None] = mapped_column(Float)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    transition_reason: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class MarketTradingCalendar(Base):
    __tablename__ = "market_trading_calendar"

    market_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class MarketRegimeHistory(Base):
    __tablename__ = "market_regime_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market_code: Mapped[str] = mapped_column(String(32), index=True)
    previous_regime: Mapped[str | None] = mapped_column(String(64))
    new_regime: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[date] = mapped_column(Date)
    ended_at: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class MemoryVersion(Base):
    __tablename__ = "memory_version"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    memory_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float)
    change_reason: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class InvestmentDecision(Base):
    __tablename__ = "investment_decision"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    query: Mapped[str | None] = mapped_column(Text)
    skill_slug: Mapped[str | None] = mapped_column(String(128))
    market_regime: Mapped[str | None] = mapped_column(String(64))
    market_features: Mapped[dict] = mapped_column(JSON, default=dict)
    thesis: Mapped[dict] = mapped_column(JSON, default=dict)
    themes: Mapped[list] = mapped_column(JSON, default=list)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    portfolio_advice: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float)
    trigger_conditions: Mapped[list] = mapped_column(JSON, default=list)
    invalidation_conditions: Mapped[list] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    tool_trace: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="active")
    evaluation_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    next_evaluation_date: Mapped[date | None] = mapped_column(Date)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_as_of: Mapped[datetime | None] = mapped_column(DateTime)
    evaluation_anchor: Mapped[str] = mapped_column(String(32), default="NEXT_SESSION_OPEN")
    benchmark_symbol: Mapped[str] = mapped_column(String(32), default="000001.SH")


class InvestmentDecisionOutcome(Base):
    __tablename__ = "investment_decision_outcome"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(36), index=True)
    evaluation_date: Mapped[date] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    portfolio_return: Mapped[float | None] = mapped_column(Float)
    excess_return: Mapped[float | None] = mapped_column(Float)
    trigger_hit: Mapped[bool | None] = mapped_column(Boolean)
    invalidation_hit: Mapped[bool | None] = mapped_column(Boolean)
    realized_metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class DecisionReview(Base):
    __tablename__ = "decision_review"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(36), index=True)
    outcome_id: Mapped[int | None] = mapped_column(Integer)
    decision_quality: Mapped[float | None] = mapped_column(Float)
    what_was_correct: Mapped[list] = mapped_column(JSON, default=list)
    what_was_wrong: Mapped[list] = mapped_column(JSON, default=list)
    root_causes: Mapped[list] = mapped_column(JSON, default=list)
    unexpected_events: Mapped[list] = mapped_column(JSON, default=list)
    lessons: Mapped[list] = mapped_column(JSON, default=list)
    memory_candidate_ids: Mapped[list] = mapped_column(JSON, default=list)
    applicable_regimes: Mapped[list] = mapped_column(JSON, default=list)
    invalidation_updates: Mapped[list] = mapped_column(JSON, default=list)
    regime_path: Mapped[list] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    review_mode: Mapped[str | None] = mapped_column(String(32))
    review_model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

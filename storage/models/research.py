from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
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
    skill_version: Mapped[int | None] = mapped_column(Integer)
    skill_contract_hash: Mapped[str | None] = mapped_column(String(64))
    skill_markdown_hash: Mapped[str | None] = mapped_column(String(64))
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
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime)
    market_feature_version: Mapped[str | None] = mapped_column(String(64))
    market_regime_confidence: Mapped[float | None] = mapped_column(Float)
    benchmark_route: Mapped[dict | None] = mapped_column(JSON)
    retrieval_context_ids: Mapped[list | None] = mapped_column(JSON)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    supervisor_version: Mapped[str | None] = mapped_column(String(32))
    participating_agents: Mapped[list | None] = mapped_column(JSON)
    decision_type: Mapped[str | None] = mapped_column(String(64))
    market: Mapped[str | None] = mapped_column(String(32))
    style: Mapped[str | None] = mapped_column(String(64))
    sector: Mapped[str | None] = mapped_column(String(128))
    opportunity_ranking_version: Mapped[str | None] = mapped_column(String(64))
    portfolio_rule_version: Mapped[str | None] = mapped_column(String(64))
    benchmark_router_version: Mapped[str | None] = mapped_column(String(64))
    regime_model_version: Mapped[str | None] = mapped_column(String(64))
    retrieval_policy_version: Mapped[str | None] = mapped_column(String(64))
    benchmark_route_input: Mapped[dict | None] = mapped_column(JSON)


class InvestmentDecisionOutcome(Base):
    __tablename__ = "investment_decision_outcome"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(36), index=True)
    evaluation_date: Mapped[date] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    portfolio_return: Mapped[float | None] = mapped_column(Float)
    excess_return: Mapped[float | None] = mapped_column(Float)
    absolute_return: Mapped[float | None] = mapped_column(Float)
    market_return: Mapped[float | None] = mapped_column(Float)
    market_excess_return: Mapped[float | None] = mapped_column(Float)
    style_return: Mapped[float | None] = mapped_column(Float)
    style_excess_return: Mapped[float | None] = mapped_column(Float)
    sector_return: Mapped[float | None] = mapped_column(Float)
    sector_excess_return: Mapped[float | None] = mapped_column(Float)
    theme_basket_return: Mapped[float | None] = mapped_column(Float)
    theme_excess_return: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    max_adverse_excursion: Mapped[float | None] = mapped_column(Float)
    max_favorable_excursion: Mapped[float | None] = mapped_column(Float)
    benchmark_route: Mapped[dict | None] = mapped_column(JSON)
    trigger_hit: Mapped[bool | None] = mapped_column(Boolean)
    invalidation_hit: Mapped[bool | None] = mapped_column(Boolean)
    realized_metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class DecisionSnapshot(Base):
    """四系统集成核心数据对象 DecisionSnapshot（设计文档 §26 / §82；详细修改方案 §5 v2）。"""

    __tablename__ = "decision_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    decision_id: Mapped[str] = mapped_column(String(36), index=True)
    decision_time: Mapped[datetime | None] = mapped_column(DateTime)
    # 详细修改方案 §5：v2 固定 Schema（runtime/proposal/policy/tools/inputs/output）。
    schema_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    market: Mapped[dict] = mapped_column(JSON, default=dict)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    factor: Mapped[dict] = mapped_column(JSON, default=dict)
    strategy: Mapped[dict] = mapped_column(JSON, default=dict)
    agent: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[dict] = mapped_column(JSON, default=dict)
    runtime: Mapped[dict] = mapped_column(JSON, default=dict)
    tools: Mapped[dict] = mapped_column(JSON, default=dict)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    portfolio: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[dict] = mapped_column(JSON, default=dict)
    lineage: Mapped[list] = mapped_column(JSON, default=list)
    decision_quality: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class DecisionSnapshotV3Record(Base):
    """Append-only v3 snapshot; the canonical contract is stored as JSON."""

    __tablename__ = "decision_snapshot_v3"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("investment_decision.id"), unique=True, index=True)
    schema_version: Mapped[str] = mapped_column(String(40), default="decision.snapshot.v3")
    bundle_id: Mapped[str] = mapped_column(ForeignKey("decision_input_bundle.bundle_id"), unique=True, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class DecisionBundleBindingRecord(Base):
    """Database-level one-to-one anchor for a formal decision/bundle pair."""

    __tablename__ = "decision_bundle_binding"

    decision_id: Mapped[str] = mapped_column(ForeignKey("investment_decision.id"), primary_key=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("decision_input_bundle.bundle_id"), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


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
    attribution_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class DecisionOutcomeRecord(Base):
    """D7 immutable outcome contract, separate from the legacy evaluator row."""

    __tablename__ = "decision_outcome_v2"

    outcome_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("investment_decision.id"), index=True)
    outcome_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class DecisionReviewV2Record(Base):
    """Structured D7 review, independent of legacy review persistence."""

    __tablename__ = "decision_review_v2"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("investment_decision.id"), index=True)
    review_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class DecisionMemoryRecord(Base):
    """Agent-owned memory table; never mixed with external evidence tables."""

    __tablename__ = "decision_memory_v2"

    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_type: Mapped[str] = mapped_column(String(40), index=True)
    memory_hash: Mapped[str] = mapped_column(String(64), unique=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="PROPOSED")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SpecialistArtifactRecord(Base):
    __tablename__ = "specialist_artifact_v2"
    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("investment_decision.id"), index=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("decision_input_bundle.bundle_id"), index=True)
    artifact_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class InvestmentProposalV2Record(Base):
    __tablename__ = "investment_proposal_v2"
    proposal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("investment_decision.id"), index=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("decision_input_bundle.bundle_id"), index=True)
    proposal_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class PolicyEvaluationRecord(Base):
    __tablename__ = "policy_evaluation_v2"
    policy_result_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("investment_decision.id"), index=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("decision_input_bundle.bundle_id"), index=True)
    result_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

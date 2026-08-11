"""Persistence records for P2 orchestration, execution and controlled evolution."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


class AgentRun(Base):
    __tablename__ = "agent_run"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task_type: Mapped[str] = mapped_column(String(64))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    as_of: Mapped[datetime | None] = mapped_column(DateTime)
    supervisor_version: Mapped[str] = mapped_column(String(32), default="v1")
    participating_agents: Mapped[list] = mapped_column(JSON, default=list)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class AgentSubtask(Base):
    __tablename__ = "agent_subtask"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(String(36), index=True)
    agent: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    conclusion: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class AgentConflictRecord(Base):
    __tablename__ = "agent_conflict"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[str] = mapped_column(String(36), index=True)
    dimension: Mapped[str] = mapped_column(String(64))
    opinions: Mapped[list] = mapped_column(JSON, default=list)
    resolution_policy: Mapped[str] = mapped_column(String(64))
    resolved_value: Mapped[dict | None] = mapped_column(JSON)
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class TradeIntentRecord(Base):
    __tablename__ = "trade_intent"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    client_order_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    decision_id: Mapped[str] = mapped_column(String(36), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    target_version: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class ExecutionOrderRecord(Base):
    __tablename__ = "execution_order"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trade_intent_id: Mapped[str] = mapped_column(String(36), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[int] = mapped_column(Integer)
    limit_price: Mapped[float | None] = mapped_column(Float)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    rejection_reasons: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class ExecutionFillRecord(Base):
    __tablename__ = "execution_fill"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    execution_order_id: Mapped[str] = mapped_column(String(36), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    broker_fill_id: Mapped[str | None] = mapped_column(String(128), index=True)
    filled_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class ExecutionOrderEventRecord(Base):
    __tablename__ = "execution_order_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_order_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class PositionSnapshotRecord(Base):
    __tablename__ = "position_snapshot"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(32))
    cash: Mapped[float] = mapped_column(Float)
    positions: Mapped[dict] = mapped_column(JSON, default=dict)


class ExecutionReconciliationRecord(Base):
    __tablename__ = "execution_reconciliation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), index=True)
    status: Mapped[str] = mapped_column(String(64))
    differences: Mapped[list] = mapped_column(JSON, default=list)


class ExecutionRuntimeState(Base):
    __tablename__ = "execution_runtime_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    halted: Mapped[bool] = mapped_column(default=False)
    halt_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class SkillProposalRecord(Base):
    __tablename__ = "skill_proposal"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_slug: Mapped[str] = mapped_column(String(128), index=True)
    base_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class SkillEvaluationRecord(Base):
    __tablename__ = "skill_evaluation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class StrategyDefinitionRecord(Base):
    __tablename__ = "strategy_definition"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class StrategyEvaluationRecord(Base):
    __tablename__ = "strategy_evaluation"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    strategy_id: Mapped[str] = mapped_column(String(36), index=True)
    evaluation_type: Mapped[str] = mapped_column(String(32))
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime)
    data_snapshot_id: Mapped[str | None] = mapped_column(String(128))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    passed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

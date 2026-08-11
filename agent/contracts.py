"""P2 multi-agent shared task contracts.

These DTOs deliberately contain references and structured data only.  A
specialist never receives a mutable service object, which makes task replay and
cross-process transport possible.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class AgentRole(StrEnum):
    SUPERVISOR = "SupervisorAgent"
    MARKET = "MarketAgent"
    RESEARCH = "ResearchAgent"
    TECHNICAL = "TechnicalAgent"
    FACTOR = "FactorAgent"
    PORTFOLIO = "PortfolioAgent"
    RISK = "RiskAgent"
    REVIEW = "ReviewAgent"


class AgentTask(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_task_id: str | None = None
    task_type: str
    objective: str
    required_outputs: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deadline_ms: int = Field(default=120_000, gt=0)
    token_budget: int = Field(default=30_000, ge=0)
    tool_budget: int = Field(default=40, ge=0)
    agent_budget: int = Field(default=6, ge=1)
    status: TaskStatus = TaskStatus.PENDING


class AgentArtifact(BaseModel):
    agent: AgentRole
    task_id: str
    status: TaskStatus = TaskStatus.SUCCESS
    conclusion: dict = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    data_as_of: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    tool_trace_ids: list[str] = Field(default_factory=list)
    token_used: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)


class AgentConflict(BaseModel):
    dimension: str
    opinions: list[dict]
    resolution_policy: str = "domain_owner_first"
    resolved_value: object | None = None
    resolved_by: str | None = None


"""P2 multi-agent shared task contracts.

These DTOs deliberately contain references and structured data only.  A
specialist never receives a mutable service object, which makes task replay and
cross-process transport possible.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from contracts.immutable import ensure_json, freeze
from engines.market.trading_clock import get_default_clock


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


class SpecialistRole(StrEnum):
    MARKET = "MARKET"
    RESEARCH = "RESEARCH"
    TECHNICAL = "TECHNICAL"
    FACTOR = "FACTOR"
    PORTFOLIO = "PORTFOLIO"
    RISK = "RISK"


class AgentTask(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_task_id: str | None = None
    task_type: str
    assigned_agent: AgentRole | None = None
    objective: str
    required_outputs: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    as_of: datetime = Field(default_factory=lambda: get_default_clock().now("CN_A"))
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


class SpecialistStatus(StrEnum):
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class ToolUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    calls: int = Field(default=0, ge=0)
    tool_names: list[str] = Field(default_factory=list)
    latency_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def freeze_names(self):
        object.__setattr__(self, "tool_names", freeze(self.tool_names))
        return self


class SpecialistArtifact(BaseModel):
    """Canonical v2 specialist output; ``unknowns`` is always machine-readable."""

    artifact_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    task_id: str = Field(min_length=1)
    model_config = ConfigDict(frozen=True, extra="forbid")

    specialist: SpecialistRole
    status: SpecialistStatus = SpecialistStatus.SUCCESS
    conclusion: dict = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    opinions: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    tool_usage: ToolUsage = Field(default_factory=ToolUsage)
    artifact_hash: str = ""

    def model_copy(self, *, update=None, deep=False):
        values = self.model_dump(mode="python")
        values.update(update or {})
        return type(self).model_validate(values)

    @field_validator("specialist", mode="before")
    @classmethod
    def coerce_specialist_role(cls, value):
        if isinstance(value, AgentRole):
            value = value.value
        if isinstance(value, str):
            aliases = {
                "MarketAgent": SpecialistRole.MARKET,
                "ResearchAgent": SpecialistRole.RESEARCH,
                "TechnicalAgent": SpecialistRole.TECHNICAL,
                "FactorAgent": SpecialistRole.FACTOR,
                "PortfolioAgent": SpecialistRole.PORTFOLIO,
                "RiskAgent": SpecialistRole.RISK,
            }
            if value in aliases:
                return aliases[value]
            try:
                return SpecialistRole(value)
            except ValueError:
                try:
                    return SpecialistRole[value.upper()]
                except KeyError as exc:
                    raise ValueError(f"unsupported specialist role: {value}") from exc
        return value

    @model_validator(mode="after")
    def compute_hash(self) -> "SpecialistArtifact":
        ensure_json(self.conclusion)
        ensure_json(self.opinions)
        payload = self.model_dump(mode="json", exclude={"artifact_hash"})
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if self.artifact_hash and self.artifact_hash != digest:
            raise ValueError("artifact_hash does not match artifact contents")
        object.__setattr__(self, "artifact_hash", digest)
        for field_name in ("conclusion", "evidence_refs", "opinions", "warnings", "unknowns"):
            object.__setattr__(self, field_name, freeze(getattr(self, field_name)))
        return self

    @property
    def agent(self) -> AgentRole:
        return AgentRole[f"{self.specialist.value}"]

    @property
    def data_as_of(self) -> None:
        return None

    @property
    def tool_calls(self) -> int:
        return self.tool_usage.calls

    @property
    def token_used(self) -> int:
        return 0

    @property
    def latency_ms(self) -> int:
        return self.tool_usage.latency_ms


AgentArtifactV2 = SpecialistArtifact


class AgentConflict(BaseModel):
    dimension: str
    opinions: list[dict]
    resolution_policy: str = "domain_owner_first"
    resolved_value: object | None = None
    resolved_by: str | None = None

"""Immutable, quant-owned post-decision outcome facts.

An outcome is an observation, not a new decision.  The contract deliberately
contains no execution/order fields and can only be constructed from a
read-only, versioned quant source.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.immutable import ensure_json, freeze
from contracts.proposal import _hash, _nonblank, _unique_refs


class DecisionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["decision-outcome.v1"] = "decision-outcome.v1"
    decision_id: str
    horizon: str
    measured_at: datetime
    available_at: datetime | None = None
    decision_as_of: datetime | None = None
    return_pct: float | None = None
    benchmark_return_pct: float | None = None
    excess_return_pct: float | None = None
    max_drawdown: float | None = None
    realized_volatility: float | None = None
    invalidation_hit: bool = False
    invalidation_hit_at: datetime | None = None
    execution_status: str | None = None
    source_system: Literal["quant"] = "quant"
    source_snapshot_refs: list[str]
    attribution: dict[str, Any] = Field(default_factory=dict)
    outcome_hash: str = ""

    @field_validator("decision_id", "horizon")
    @classmethod
    def required_text(cls, value: str) -> str:
        return _nonblank(value, "outcome identifier")

    @field_validator("measured_at", "available_at", "decision_as_of", "invalidation_hit_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("outcome times must be timezone-aware")
        return value

    @field_validator("source_snapshot_refs")
    @classmethod
    def refs(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("outcome requires quant source_snapshot_refs")
        return freeze(_unique_refs(value, "source_snapshot_refs"))

    @model_validator(mode="after")
    def validate_temporal_and_hash(self) -> "DecisionOutcome":
        if self.available_at is not None and self.available_at > self.measured_at:
            raise ValueError("available_at must be <= measured_at")
        if self.decision_as_of is not None and self.measured_at < self.decision_as_of:
            raise ValueError("outcome cannot precede decision_as_of (no look-ahead)")
        # Trading-session semantics belong to OutcomeService, which receives
        # the quant-owned/injected calendar.  Keeping this contract free of
        # domain imports ensures pure validation is deterministic and cannot
        # accidentally initialize a broker/QMT calendar.
        if self.invalidation_hit and self.invalidation_hit_at is None:
            raise ValueError("invalidation_hit_at is required when invalidation_hit is true")
        if self.invalidation_hit_at is not None and self.invalidation_hit_at > self.measured_at:
            raise ValueError("invalidation_hit_at must be <= measured_at")
        ensure_json(self.attribution)
        object.__setattr__(self, "attribution", freeze(self.attribution))
        digest = _hash(self.model_dump(exclude={"outcome_hash"}, mode="json"))
        if self.outcome_hash and self.outcome_hash != digest:
            raise ValueError("outcome_hash does not match canonical outcome")
        object.__setattr__(self, "outcome_hash", digest)
        return self

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> "DecisionOutcome":
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(mode="python"))

    @classmethod
    def build(cls, **values: Any) -> "DecisionOutcome":
        values.pop("outcome_hash", None)
        return cls.model_validate(values)


__all__ = ["DecisionOutcome"]

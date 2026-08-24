"""Canonical policy evaluation and final decision contracts (v2)."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.immutable import ensure_json, freeze
from contracts.proposal import _canonical, _hash, _nonblank, _unique_refs


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PolicyCheckResult(_FrozenModel):
    rule_id: str
    rule_version: str
    passed: bool
    severity: Literal["INFO", "WARN", "ADJUST", "REJECT"]
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    original_value: Any | None = None
    adjusted_value: Any | None = None
    reason_code: str
    reason: str

    @field_validator("rule_id", "rule_version", "reason_code")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _nonblank(value, "policy identifier")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def freeze_snapshot(self) -> "PolicyCheckResult":
        ensure_json(self.input_snapshot)
        if self.original_value is not None:
            ensure_json(self.original_value)
        if self.adjusted_value is not None:
            ensure_json(self.adjusted_value)
        object.__setattr__(self, "input_snapshot", freeze(self.input_snapshot))
        object.__setattr__(self, "original_value", freeze(self.original_value))
        object.__setattr__(self, "adjusted_value", freeze(self.adjusted_value))
        return self

    @model_validator(mode="after")
    def validate_semantics(self) -> "PolicyCheckResult":
        if not self.reason_code.strip() or not self.reason.strip():
            raise ValueError("policy checks require reason_code and reason")
        if not self.passed and self.severity == "INFO":
            raise ValueError("failed policy checks cannot have INFO severity")
        if self.severity == "ADJUST" and self.adjusted_value is None:
            raise ValueError("ADJUST checks require adjusted_value")
        if self.severity == "REJECT" and self.passed:
            raise ValueError("REJECT checks must fail")
        return self

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> "PolicyCheckResult":
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(mode="python"))


class PolicyEvaluation(_FrozenModel):
    """Ordered, hash-bearing authorization trace produced by PolicyEngine v2."""

    policy_result_id: str
    policy_version: str
    checks: list[PolicyCheckResult]
    approved: bool
    original_value: Any | None = None
    adjusted_value: Any | None = None
    result_hash: str

    @field_validator("policy_result_id", "policy_version")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _nonblank(value, "policy identifier")

    @model_validator(mode="after")
    def validate_trace_and_hash(self) -> "PolicyEvaluation":
        if not self.checks:
            raise ValueError("policy evaluation must contain an ordered check trace")
        if self.original_value is not None:
            ensure_json(self.original_value)
        if self.adjusted_value is not None:
            ensure_json(self.adjusted_value)
        object.__setattr__(self, "checks", freeze(self.checks))
        object.__setattr__(self, "original_value", freeze(self.original_value))
        object.__setattr__(self, "adjusted_value", freeze(self.adjusted_value))
        reject_checks = [check for check in self.checks if check.severity == "REJECT" and not check.passed]
        if self.approved and reject_checks:
            raise ValueError("approved policy evaluation cannot contain rejected checks")
        adjustments = [check for check in self.checks if check.severity == "ADJUST" and check.adjusted_value is not None]
        if adjustments and self.adjusted_value != adjustments[-1].adjusted_value:
            raise ValueError("adjusted_value must equal the last ordered adjustment")
        digest = _hash(self.model_dump(exclude={"result_hash"}, mode="json"))
        if not self.result_hash or self.result_hash != digest:
            raise ValueError("result_hash does not match canonical policy evaluation")
        return self

    @classmethod
    def build(cls, **values: Any) -> "PolicyEvaluation":
        values.pop("result_hash", None)
        values["result_hash"] = _hash(values)
        return cls.model_validate(values)

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> "PolicyEvaluation":
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(mode="python"))

    @property
    def policy_result_id_hash(self) -> str:
        return self.result_hash


class FinalInvestmentDecision(_FrozenModel):
    decision_id: str
    schema_version: Literal["investment-decision.v2"] = "investment-decision.v2"
    decision_time: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    subject_type: Literal["SYMBOL", "THEME", "PORTFOLIO"]
    subject_key: str | None
    decision_action: Literal["APPROVE", "APPROVE_WITH_ADJUSTMENT", "VETO"]
    investment_action: Literal["BUY", "SELL", "HOLD", "INCREASE", "REDUCE", "EXIT", "WATCH"]
    target_weight: float | None
    weight_delta: float | None
    confidence: float = Field(ge=0, le=1)
    decision_quality: str
    rationale: list[str]
    evidence_refs: list[str]
    proposal_id: str
    policy_result_id: str
    invalidation_conditions: list[str]
    bundle_id: str
    snapshot_id: str | None = None
    decision_hash: str

    @field_validator("decision_id", "decision_quality", "proposal_id", "policy_result_id", "bundle_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _nonblank(value, "decision field")

    @field_validator("subject_key")
    @classmethod
    def validate_subject_key(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "subject_key")

    @field_validator("decision_time", "valid_from", "valid_until")
    @classmethod
    def validate_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("decision validity times must be timezone-aware")
        return value

    @field_validator("target_weight")
    @classmethod
    def validate_weight(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 1:
            raise ValueError("target_weight must be between 0 and 1")
        return value

    @field_validator("weight_delta")
    @classmethod
    def validate_delta(cls, value: float | None) -> float | None:
        if value is not None and not -1 <= value <= 1:
            raise ValueError("weight_delta must be between -1 and 1")
        return value

    @field_validator("rationale", "invalidation_conditions")
    @classmethod
    def freeze_text(cls, value: list[str]) -> list[str]:
        return freeze([_nonblank(item, "decision text") for item in value])

    @field_validator("evidence_refs")
    @classmethod
    def freeze_refs(cls, value: list[str]) -> list[str]:
        return freeze(_unique_refs(value, "evidence_refs"))

    @model_validator(mode="after")
    def validate_semantics_and_hash(self) -> "FinalInvestmentDecision":
        if self.subject_type in {"SYMBOL", "THEME"} and self.subject_key is None:
            raise ValueError("subject_key is required for SYMBOL and THEME decisions")
        if self.target_weight is not None and self.weight_delta is not None:
            raise ValueError("target_weight and weight_delta are mutually exclusive")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        if self.valid_from < self.decision_time:
            raise ValueError("valid_from cannot precede decision_time")
        if self.decision_action == "VETO":
            if self.target_weight is not None or self.weight_delta is not None:
                raise ValueError("VETO decisions cannot carry target weight or weight delta")
            if not self.rationale:
                raise ValueError("VETO decisions require a nonblank governance rationale")
        elif self.decision_action == "APPROVE_WITH_ADJUSTMENT" and self.target_weight is None and self.weight_delta is None:
            raise ValueError("adjusted approvals require an adjusted target or weight delta")
        elif self.decision_action in {"APPROVE", "APPROVE_WITH_ADJUSTMENT"}:
            if self.investment_action in {"BUY", "INCREASE"} and self.target_weight is None and self.weight_delta is None:
                raise ValueError("approved increases require target_weight or weight_delta")
            if self.investment_action in {"SELL", "REDUCE", "EXIT"} and self.target_weight is None and self.weight_delta is None:
                raise ValueError("approved reductions require target_weight or weight_delta")
            if not self.evidence_refs or not self.rationale:
                raise ValueError("actionable approvals require evidence_refs and rationale")
        if self.investment_action in {"BUY", "INCREASE"} and self.weight_delta is not None and self.weight_delta < 0:
            raise ValueError("buy/increase weight_delta must be non-negative")
        if self.investment_action in {"SELL", "REDUCE", "EXIT"} and self.weight_delta is not None and self.weight_delta > 0:
            raise ValueError("sell/reduce/exit weight_delta must be non-positive")
        if self.investment_action in {"HOLD", "WATCH"} and (self.target_weight is not None or self.weight_delta is not None):
            raise ValueError(f"{self.investment_action} cannot carry target weight or weight delta")
        material = self.model_dump(exclude={"decision_hash"}, mode="python")
        digest = _hash(material)
        if not self.decision_hash or self.decision_hash != digest:
            raise ValueError("decision_hash does not match canonical final decision")
        return self

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> "FinalInvestmentDecision":
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(mode="python"))

    @classmethod
    def build(cls, **values: Any) -> "FinalInvestmentDecision":
        values.pop("decision_hash", None)
        values.setdefault("schema_version", "investment-decision.v2")
        values.setdefault("valid_until", None)
        values.setdefault("snapshot_id", None)
        values["decision_hash"] = _hash(values)
        return cls.model_validate(values)


__all__ = ["PolicyCheckResult", "PolicyEvaluation", "FinalInvestmentDecision"]

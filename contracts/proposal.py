"""Canonical investment proposal contracts (v2).

The proposal is a business object, not a narrative response and not an order.
All values which participate in the proposal hash are frozen after validation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.immutable import ensure_json, freeze


def _canonical(value: Any) -> str:
    """Return deterministic JSON for JSON-compatible values and datetimes."""
    def normalize(item: Any) -> Any:
        if isinstance(item, datetime):
            if item.tzinfo is None or item.utcoffset() is None:
                raise ValueError("datetime values must be timezone-aware")
            return item.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(item, BaseModel):
            return normalize(item.model_dump(mode="json"))
        if isinstance(item, dict):
            return {str(k): normalize(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(v) for v in item]
        return item

    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _nonblank(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank")
    return value.strip()


def _unique_refs(value: list[str], name: str) -> list[str]:
    cleaned = [_nonblank(item, name) for item in value]
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{name} must not contain duplicates")
    return cleaned


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DecisionHorizon(_FrozenModel):
    """The intended holding horizon for a proposal.

    ``start`` and ``end`` are optional because a horizon can be expressed as a
    named period, but any supplied times must be aware and ordered.
    """

    period: str
    start: datetime | None = None
    end: datetime | None = None

    @field_validator("period")
    @classmethod
    def validate_period(cls, value: str) -> str:
        return _nonblank(value, "period")

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("horizon times must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_order(self) -> "DecisionHorizon":
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("horizon end must be after start")
        return self


class ThesisPoint(_FrozenModel):
    statement: str
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: str) -> str:
        return _nonblank(value, "statement")

    @field_validator("evidence_refs")
    @classmethod
    def validate_refs(cls, value: list[str]) -> list[str]:
        return freeze(_unique_refs(value, "evidence_refs"))


class ModelIdentity(_FrozenModel):
    provider: str
    model: str
    model_version: str | None = None

    @field_validator("provider", "model")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _nonblank(value, "model identity")


class NarrativeReport(_FrozenModel):
    """Human/UI text kept separate from the canonical proposal."""

    title: str | None = None
    summary: str
    sections: dict[str, str] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _nonblank(value, "summary")

    @model_validator(mode="after")
    def freeze_values(self) -> "NarrativeReport":
        object.__setattr__(self, "sections", freeze(dict(self.sections)))
        return self


class InvestmentProposalV2(_FrozenModel):
    proposal_id: str
    schema_version: Literal["investment-proposal.v2"] = "investment-proposal.v2"
    subject_type: Literal["SYMBOL", "THEME", "PORTFOLIO"]
    subject_key: str | None
    action: Literal["BUY", "SELL", "HOLD", "INCREASE", "REDUCE", "EXIT", "WATCH"]
    target_weight: float | None
    weight_delta: float | None
    confidence: float = Field(ge=0, le=1)
    horizon: DecisionHorizon
    thesis: list[ThesisPoint]
    catalysts: list[str] = Field(default_factory=list)
    entry_conditions: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    expected_risks: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    specialist_artifact_refs: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    generated_by: ModelIdentity
    proposal_hash: str

    @field_validator("proposal_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _nonblank(value, "proposal_id")

    @field_validator("subject_key")
    @classmethod
    def validate_subject_key(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "subject_key")

    @field_validator("target_weight")
    @classmethod
    def validate_target_weight(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 1:
            raise ValueError("target_weight must be between 0 and 1")
        return value

    @field_validator("weight_delta")
    @classmethod
    def validate_weight_delta(cls, value: float | None) -> float | None:
        if value is not None and not -1 <= value <= 1:
            raise ValueError("weight_delta must be between -1 and 1")
        return value

    @field_validator("catalysts", "entry_conditions", "invalidation_conditions", "expected_risks", "unknowns")
    @classmethod
    def freeze_text_lists(cls, value: list[str]) -> list[str]:
        return freeze([_nonblank(item, "text item") for item in value])

    @field_validator("thesis")
    @classmethod
    def freeze_thesis(cls, value: list[ThesisPoint]) -> list[ThesisPoint]:
        return freeze(value)

    @field_validator("evidence_refs", "specialist_artifact_refs")
    @classmethod
    def freeze_ref_lists(cls, value: list[str], info) -> list[str]:
        return freeze(_unique_refs(value, info.field_name))

    @model_validator(mode="after")
    def validate_and_hash(self) -> "InvestmentProposalV2":
        if self.subject_type in {"SYMBOL", "THEME"} and self.subject_key is None:
            raise ValueError("subject_key is required for SYMBOL and THEME proposals")
        if self.target_weight is not None and self.weight_delta is not None:
            raise ValueError("target_weight and weight_delta are mutually exclusive")
        if self.action in {"HOLD", "WATCH"} and (self.target_weight is not None or self.weight_delta is not None):
            raise ValueError(f"{self.action} cannot carry a target weight or weight delta")
        if self.action in {"BUY", "INCREASE", "SELL", "REDUCE", "EXIT"} and self.target_weight is None and self.weight_delta is None:
            raise ValueError(f"{self.action} requires target_weight or weight_delta")
        if self.action in {"BUY", "INCREASE"} and self.weight_delta is not None and self.weight_delta < 0:
            raise ValueError(f"{self.action} requires a non-negative weight_delta")
        if self.action in {"SELL", "REDUCE", "EXIT"} and self.weight_delta is not None and self.weight_delta > 0:
            raise ValueError(f"{self.action} requires a non-positive weight_delta")
        material = self.model_dump(exclude={"proposal_hash"}, mode="python")
        digest = _hash(material)
        if not self.proposal_hash or self.proposal_hash != digest:
            raise ValueError("proposal_hash does not match canonical proposal contents")
        return self

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> "InvestmentProposalV2":
        """Do not allow Pydantic's unchecked copy/update to forge a hash."""
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(mode="python"))

    @classmethod
    def build(cls, **values: Any) -> "InvestmentProposalV2":
        values.pop("proposal_hash", None)
        values.setdefault("schema_version", "investment-proposal.v2")
        values["proposal_hash"] = _hash(values)
        return cls.model_validate(values)

    @classmethod
    def from_v1(cls, proposal: Any, *, proposal_id: str, horizon: DecisionHorizon, generated_by: ModelIdentity, evidence_refs: list[str] | None = None) -> "InvestmentProposalV2":
        action = str(proposal.action).upper()
        subject_type = "SYMBOL"
        return cls.build(
            proposal_id=proposal_id,
            subject_type=subject_type,
            subject_key=proposal.symbol,
            action=action,
            target_weight=proposal.proposed_weight if action not in {"HOLD", "WATCH"} else None,
            weight_delta=None,
            confidence=proposal.confidence,
            horizon=horizon,
            thesis=[ThesisPoint(statement="v1 proposal", evidence_refs=list(getattr(proposal, "thesis_refs", [])))],
            catalysts=[], entry_conditions=[], invalidation_conditions=[], expected_risks=[],
            evidence_refs=evidence_refs or [], specialist_artifact_refs=[], unknowns=[], generated_by=generated_by,
        )

    def to_v1(self) -> Any:
        from engines.policy.models import InvestmentProposal
        return InvestmentProposal(
            symbol=self.subject_key or "",
            action=self.action,
            proposed_weight=float(self.target_weight or 0.0),
            confidence=self.confidence,
            thesis_refs=list(self.evidence_refs),
            evidence_count=len(self.evidence_refs),
        )


def proposal_v1_to_v2(proposal: Any, *, proposal_id: str, horizon: DecisionHorizon, generated_by: ModelIdentity, evidence_refs: list[str] | None = None) -> InvestmentProposalV2:
    """Explicit compatibility adapter; v1 remains a separate dataclass API."""
    return InvestmentProposalV2.from_v1(
        proposal, proposal_id=proposal_id, horizon=horizon,
        generated_by=generated_by, evidence_refs=evidence_refs,
    )


def proposal_v2_to_v1(proposal: InvestmentProposalV2) -> Any:
    return proposal.to_v1()


adapt_v1_to_v2 = proposal_v1_to_v2
adapt_v2_to_v1 = proposal_v2_to_v1


__all__ = [
    "DecisionHorizon", "ThesisPoint", "ModelIdentity", "NarrativeReport", "InvestmentProposalV2",
    "proposal_v1_to_v2", "proposal_v2_to_v1", "adapt_v1_to_v2", "adapt_v2_to_v1",
]

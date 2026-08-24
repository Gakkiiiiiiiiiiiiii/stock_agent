"""Structured decision review; narrative alone cannot create memory."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.decision_memory import DecisionMemoryCandidate
from contracts.immutable import ensure_json, freeze
from contracts.proposal import _hash, _nonblank, _unique_refs


class ReviewPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str
    statement: str
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("category", "statement")
    @classmethod
    def required_text(cls, value: str) -> str:
        return _nonblank(value, "review point")

    @field_validator("evidence_refs")
    @classmethod
    def refs(cls, value: list[str]) -> list[str]:
        return freeze(_unique_refs(value, "review evidence_refs"))


class DecisionReview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    review_id: str
    schema_version: Literal["decision-review.v1"] = "decision-review.v1"
    decision_id: str
    outcome_refs: list[str]
    correct_judgments: list[ReviewPoint] = Field(default_factory=list)
    incorrect_judgments: list[ReviewPoint] = Field(default_factory=list)
    missed_evidence: list[ReviewPoint] = Field(default_factory=list)
    overweighted_evidence: list[ReviewPoint] = Field(default_factory=list)
    underweighted_evidence: list[ReviewPoint] = Field(default_factory=list)
    policy_effectiveness: list[ReviewPoint] = Field(default_factory=list)
    confidence_calibration_error: float | None = None
    proposed_memory_items: list[DecisionMemoryCandidate] = Field(default_factory=list)
    created_at: datetime
    review_hash: str = ""

    @field_validator("review_id", "decision_id")
    @classmethod
    def ids(cls, value: str) -> str:
        return _nonblank(value, "review id")

    @field_validator("outcome_refs")
    @classmethod
    def outcome_ids(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("review requires outcome_refs")
        return freeze(_unique_refs(value, "outcome_refs"))

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_review(self) -> "DecisionReview":
        digest = _hash(self.model_dump(exclude={"review_hash"}, mode="json"))
        if self.review_hash and self.review_hash != digest:
            raise ValueError("review_hash does not match canonical review")
        object.__setattr__(self, "correct_judgments", freeze(self.correct_judgments))
        object.__setattr__(self, "incorrect_judgments", freeze(self.incorrect_judgments))
        object.__setattr__(self, "missed_evidence", freeze(self.missed_evidence))
        object.__setattr__(self, "overweighted_evidence", freeze(self.overweighted_evidence))
        object.__setattr__(self, "underweighted_evidence", freeze(self.underweighted_evidence))
        object.__setattr__(self, "policy_effectiveness", freeze(self.policy_effectiveness))
        object.__setattr__(self, "proposed_memory_items", freeze(self.proposed_memory_items))
        object.__setattr__(self, "review_hash", digest)
        return self

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> "DecisionReview":
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(mode="python"))

    @classmethod
    def build(cls, **values: Any) -> "DecisionReview":
        values.pop("review_hash", None)
        return cls.model_validate(values)


__all__ = ["ReviewPoint", "DecisionReview"]

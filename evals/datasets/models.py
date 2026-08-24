from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.evidence import EvidenceType


def _nonblank(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-blank")
    return value.strip()


def _unique(values: list[str], field: str) -> list[str]:
    cleaned = [_nonblank(value, field) for value in values]
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{field} must not contain duplicates")
    return cleaned


class EvalExpected(BaseModel):
    """Labels and deterministic constraints for one decision case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_evidence_types: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    acceptable_actions: list[str] = Field(default_factory=list)
    max_target_weight: float | None = Field(default=None, ge=0, le=1)
    required_specialists: list[str] = Field(default_factory=list)
    # This optional field is intentionally explicit rather than inferred from
    # prose.  It is useful for false/missed VETO evaluation.
    requires_veto: bool = False

    @field_validator("required_evidence_types")
    @classmethod
    def validate_evidence_types(cls, values: list[str]) -> list[str]:
        cleaned = _unique(values, "required_evidence_types")
        allowed = {member.value for member in EvidenceType}
        unknown = [value for value in cleaned if value not in allowed]
        if unknown:
            raise ValueError(f"unknown evidence type(s): {', '.join(unknown)}")
        return cleaned

    @field_validator("forbidden_claims", "risk_flags", "acceptable_actions", "required_specialists")
    @classmethod
    def validate_text_lists(cls, values: list[str], info) -> list[str]:
        return _unique(values, info.field_name)


class DecisionEvalCase(BaseModel):
    """A single labelled case; it contains no data payload itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    decision_time: datetime
    task: str
    bundle_id: str
    expected: EvalExpected

    @field_validator("case_id", "task", "bundle_id")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _nonblank(value, info.field_name)

    @field_validator("decision_time")
    @classmethod
    def validate_decision_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision_time must be timezone-aware")
        return value


class EvalDataset(BaseModel):
    """Ordered cases with unique IDs and deterministic JSON serialization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: list[DecisionEvalCase]

    @model_validator(mode="after")
    def unique_case_ids(self) -> "EvalDataset":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate eval case_id")
        return self

    def ordered(self) -> tuple[DecisionEvalCase, ...]:
        return tuple(sorted(self.cases, key=lambda case: case.case_id))

    def model_dump_json_deterministic(self) -> str:
        rows = [case.model_dump(mode="json") for case in self.ordered()]
        return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_jsonl(path: str | Path) -> EvalDataset:
    """Load a case JSONL file without reading any repository data directories.

    Blank lines are ignored.  Every nonblank line must be one JSON object and
    errors include the source line number to keep malformed datasets actionable.
    """

    source = Path(path)
    if not source.is_file():
        raise ValueError(f"eval dataset must be a regular file: {source}")
    cases: list[DecisionEvalCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"eval case at line {line_number} must be a JSON object")
        try:
            case = DecisionEvalCase.model_validate(value)
        except Exception as exc:
            raise ValueError(f"invalid eval case at line {line_number}: {exc}") from exc
        if case.case_id in seen:
            raise ValueError(f"duplicate eval case_id at line {line_number}: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return EvalDataset(cases=cases)


__all__ = ["DecisionEvalCase", "EvalExpected", "EvalDataset", "load_jsonl"]

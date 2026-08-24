"""Frozen input bundle contracts used by replayable decisions."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import re

from contracts.evidence import DependencyStatus, Evidence, canonical_payload
from contracts.immutable import ensure_json, freeze


class SnapshotRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(min_length=1)
    source_system: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def as_of_must_be_tz_aware(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("snapshot as_of must be timezone-aware")
        return value


class DecisionMemoryRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str = Field(min_length=1)
    source_system: str = "stock_agent"
    evidence_type: str = "DECISION_MEMORY"
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def owned_by_agent(self) -> "DecisionMemoryRef":
        if self.source_system != "stock_agent" or self.evidence_type != "DECISION_MEMORY":
            raise ValueError("decision memory must be stock_agent-owned DECISION_MEMORY")
        return self


class DecisionInputBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["decision-input.v1"] = "decision-input.v1"
    created_at: datetime
    decision_time: datetime
    task_type: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    subjects: list[str] = Field(default_factory=list)
    market: SnapshotRef | None = None
    content: SnapshotRef | None = None
    factor: SnapshotRef | None = None
    portfolio: SnapshotRef | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    decision_memories: list[DecisionMemoryRef] = Field(default_factory=list)
    dependency_status: list[DependencyStatus] = Field(default_factory=list)
    query_context: dict[str, Any] = Field(default_factory=dict)
    strategy_context: dict[str, Any] = Field(default_factory=dict)
    bundle_hash: str = ""

    def model_copy(self, *, update=None, deep=False):
        values = self.model_dump(mode="python")
        values.update(update or {})
        return type(self).model_validate(values)

    @model_validator(mode="after")
    def validate_and_hash(self) -> "DecisionInputBundle":
        if self.decision_time.tzinfo is None or self.decision_time.utcoffset() is None:
            raise ValueError("decision_time must be timezone-aware")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        for item in self.evidence:
            if item.available_at > self.decision_time:
                raise ValueError(f"look-ahead evidence: {item.evidence_id}")
        digest = compute_bundle_hash(self)
        if self.bundle_hash and self.bundle_hash != digest:
            raise ValueError("bundle_hash does not match immutable bundle contents")
        object.__setattr__(self, "bundle_hash", digest)
        for field_name in ("subjects", "evidence", "decision_memories", "dependency_status", "query_context", "strategy_context"):
            object.__setattr__(self, field_name, freeze(getattr(self, field_name)))
        return self


class DecisionInputBundlePatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    patch_id: str = Field(default_factory=lambda: str(uuid4()))
    reason: str
    created_at: datetime
    evidence: list[Evidence] = Field(default_factory=list)
    previous_hash: str
    new_hash: str

    def model_copy(self, *, update=None, deep=False):
        values = self.model_dump(mode="python")
        values.update(update or {})
        return type(self).model_validate(values)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("patch reason must not be blank")
        return value

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("patch created_at must be timezone-aware")
        return value

    @field_validator("previous_hash", "new_hash")
    @classmethod
    def hashes_required(cls, value: str) -> str:
        if not value or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("patch hashes must be lowercase sha256 values")
        return value

    @model_validator(mode="after")
    def freeze_evidence(self):
        object.__setattr__(self, "evidence", freeze(self.evidence))
        return self


def compute_bundle_hash(bundle: DecisionInputBundle) -> str:
    payload = bundle.model_dump(mode="json", exclude={"bundle_hash"})
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def build_bundle(**kwargs: Any) -> DecisionInputBundle:
    evidence = []
    seen: dict[str, Evidence] = {}
    for item in kwargs.pop("evidence", []) or []:
        evidence_item = item if isinstance(item, Evidence) else Evidence.model_validate(item)
        existing = seen.get(evidence_item.evidence_id or "")
        if existing is None:
            evidence.append(evidence_item)
            seen[evidence_item.evidence_id or ""] = evidence_item
        elif existing.model_dump(mode="json") != evidence_item.model_dump(mode="json"):
            raise ValueError(f"conflicting duplicate evidence id: {evidence_item.evidence_id}")
    return DecisionInputBundle(evidence=evidence, **kwargs)


def apply_bundle_patch(bundle: DecisionInputBundle, *, reason: str, evidence: list[Evidence], created_at: datetime | None = None) -> tuple[DecisionInputBundle, DecisionInputBundlePatch]:
    if not reason.strip():
        raise ValueError("patch reason must not be blank")
    if created_at is None:
        raise ValueError("patch created_at must be supplied by an injected clock")
    patch_time = created_at
    if patch_time.tzinfo is None or patch_time.utcoffset() is None:
        raise ValueError("patch created_at must be timezone-aware")
    if bundle.bundle_hash != compute_bundle_hash(bundle):
        raise ValueError("previous bundle hash does not match contents")
    merged = list(bundle.evidence)
    known = {item.evidence_id: item for item in merged}
    for item in evidence:
        if item.available_at > bundle.decision_time:
            raise ValueError(f"look-ahead evidence: {item.evidence_id}")
        old = known.get(item.evidence_id)
        if old is None:
            merged.append(item)
            known[item.evidence_id] = item
        elif old.model_dump(mode="json") != item.model_dump(mode="json"):
            raise ValueError(f"conflicting duplicate evidence id: {item.evidence_id}")
    if len(merged) == len(bundle.evidence):
        raise ValueError("empty or duplicate-only no-op bundle patch is not allowed")
    values = bundle.model_dump()
    values["evidence"] = merged
    values["bundle_hash"] = ""
    updated = DecisionInputBundle(**values)
    patch = DecisionInputBundlePatch(reason=reason, created_at=patch_time, evidence=evidence, previous_hash=bundle.bundle_hash, new_hash=updated.bundle_hash)
    return updated, patch


__all__ = ["DecisionInputBundle", "DecisionInputBundlePatch", "DecisionMemoryRef", "SnapshotRef", "apply_bundle_patch", "build_bundle", "compute_bundle_hash"]

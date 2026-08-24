"""Agent-owned decision experience, physically distinct from external facts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.immutable import ensure_json, freeze
from contracts.proposal import _hash, _nonblank, _unique_refs

MemoryType = Literal[
    "DECISION_CASE", "FAILURE_PATTERN", "SUCCESS_PATTERN", "REGIME_EXPERIENCE",
    "RISK_MISS", "POLICY_ADJUSTMENT", "USER_PREFERENCE",
]


class DecisionMemoryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_type: MemoryType
    content: str
    source_decision_ids: list[str] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("content")
    @classmethod
    def content_required(cls, value: str) -> str:
        return _nonblank(value, "memory content")

    @field_validator("source_decision_ids")
    @classmethod
    def source_ids(cls, value: list[str]) -> list[str]:
        return freeze(_unique_refs(value, "source_decision_ids"))

    @model_validator(mode="after")
    def validate_scope(self) -> "DecisionMemoryCandidate":
        for value in (self.valid_from, self.valid_to):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("memory validity times must be timezone-aware")
        if self.valid_from is not None and self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("memory valid_to must be after valid_from")
        ensure_json(self.scope)
        object.__setattr__(self, "scope", freeze(self.scope))
        return self


class DecisionMemory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["decision-memory.v1"] = "decision-memory.v1"
    memory_type: MemoryType
    source_decision_ids: list[str] = Field(default_factory=list)
    content: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)
    validity: dict[str, Any] = Field(default_factory=dict)
    status: Literal["PROPOSED", "ACTIVE", "SUPERSEDED", "REJECTED"] = "PROPOSED"
    confidence: float = Field(ge=0, le=1)
    weight: float = Field(default=0.25, ge=0, le=0.5)
    dedupe_key: str = ""
    source_system: Literal["stock_agent"] = "stock_agent"
    evidence_type: Literal["DECISION_MEMORY"] = "DECISION_MEMORY"
    created_at: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    memory_hash: str = ""

    @field_validator("content", "dedupe_key")
    @classmethod
    def text(cls, value: str) -> str:
        return _nonblank(value, "memory text")

    @field_validator("source_decision_ids")
    @classmethod
    def source_ids(cls, value: list[str]) -> list[str]:
        return freeze(_unique_refs(value, "source_decision_ids"))

    @field_validator("created_at", "valid_from", "valid_to")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("memory times must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_memory(self) -> "DecisionMemory":
        if not self.source_decision_ids:
            raise ValueError("decision memory requires source_decision_ids")
        if self.valid_from is not None and self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("memory valid_to must be after valid_from")
        ensure_json(self.provenance)
        ensure_json(self.scope)
        ensure_json(self.validity)
        if not self.dedupe_key:
            object.__setattr__(self, "dedupe_key", _hash({"memory_type": self.memory_type, "content": self.content, "scope": self.scope}))
        def _memory_reference(value: Any) -> bool:
            if isinstance(value, dict):
                return any(_memory_reference(key) or _memory_reference(item) for key, item in value.items())
            if isinstance(value, (list, tuple, set, frozenset)):
                return any(_memory_reference(item) for item in value)
            if isinstance(value, str):
                lowered = value.lower()
                return self.memory_id == value or "decision_memory" in lowered or "decision-memory:" in lowered
            return False
        if _memory_reference(self.provenance):
            raise ValueError("decision memory cannot cite itself or another memory as external provenance")
        object.__setattr__(self, "provenance", freeze(self.provenance))
        object.__setattr__(self, "scope", freeze(self.scope))
        object.__setattr__(self, "validity", freeze(self.validity))
        digest = _hash(self.model_dump(exclude={"memory_hash"}, mode="json"))
        if self.memory_hash and self.memory_hash != digest:
            raise ValueError("memory_hash does not match canonical memory")
        object.__setattr__(self, "memory_hash", digest)
        return self

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> "DecisionMemory":
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(mode="python"))

    @classmethod
    def build(cls, **values: Any) -> "DecisionMemory":
        values.pop("memory_hash", None)
        return cls.model_validate(values)

    def to_evidence(self, *, available_at: datetime | None = None):
        """Expose memory only as explicitly marked, low-weight agent evidence."""
        from contracts.evidence import Evidence, EvidenceQuality, EvidenceType, SourceSystem
        available = available_at or self.created_at
        return Evidence(
            evidence_type=EvidenceType.DECISION_MEMORY, source_system=SourceSystem.STOCK_AGENT,
            source_ref=f"decision-memory:{self.memory_id}", subject_type="decision",
            subject_key=self.memory_id, as_of=self.created_at, available_at=available,
            snapshot_id=self.memory_id, contract_version=self.schema_version,
            payload={"content": self.content, "memory_type": self.memory_type, "scope": dict(self.scope), "weight": min(self.weight, 0.25), "external_coverage": False},
            quality_status=EvidenceQuality.DEGRADED, confidence=min(self.confidence, 0.5),
        )


__all__ = ["MemoryType", "DecisionMemoryCandidate", "DecisionMemory"]

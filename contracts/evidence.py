"""Immutable, versioned evidence contracts for Decision Authority inputs."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from contracts.immutable import ensure_json, freeze


class EvidenceType(StrEnum):
    MARKET_SNAPSHOT = "MARKET_SNAPSHOT"
    MARKET_REGIME = "MARKET_REGIME"
    MARKET_BREADTH = "MARKET_BREADTH"
    SECTOR_STRENGTH = "SECTOR_STRENGTH"
    TECHNICAL_SIGNAL = "TECHNICAL_SIGNAL"
    TECHNICAL_PROFILE = "TECHNICAL_PROFILE"
    LIQUIDITY = "LIQUIDITY"
    FACTOR_SCORE = "FACTOR_SCORE"
    FACTOR_SET = "FACTOR_SET"
    FACTOR_RESEARCH_RESULT = "FACTOR_RESEARCH_RESULT"
    KNOWLEDGE_CLAIM = "KNOWLEDGE_CLAIM"
    CATALYST = "CATALYST"
    RISK_EVENT = "RISK_EVENT"
    VALUATION_FACT = "VALUATION_FACT"
    EARNINGS_FACT = "EARNINGS_FACT"
    PORTFOLIO_POSITION = "PORTFOLIO_POSITION"
    PORTFOLIO_EXPOSURE = "PORTFOLIO_EXPOSURE"
    PORTFOLIO_RISK = "PORTFOLIO_RISK"
    BACKTEST_RESULT = "BACKTEST_RESULT"
    DECISION_MEMORY = "DECISION_MEMORY"


class SourceSystem(StrEnum):
    QUANT = "quant"
    FACTOR = "factor"
    CONTENT = "content"
    STOCK_AGENT = "stock_agent"


class EvidenceQuality(StrEnum):
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    UNVERIFIED = "UNVERIFIED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    REJECTED = "REJECTED"


class DependencyStatusValue(StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"


class EvidenceLineage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_ref: str = Field(min_length=1)
    transform: str | None = None
    source_version: str | None = None
    observed_at: datetime | None = None

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_tz_aware(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("observed_at must be timezone-aware")
        return value


class DependencyStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    system: SourceSystem
    status: DependencyStatusValue
    checked_at: datetime
    contract_version: str | None = None
    service_version: str | None = None
    snapshot_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)

    def model_copy(self, *, update=None, deep=False):
        values = self.model_dump(mode="python")
        values.update(update or {})
        return type(self).model_validate(values)

    @field_validator("checked_at")
    @classmethod
    def checked_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("checked_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def freeze_reason_codes(self):
        object.__setattr__(self, "reason_codes", freeze(self.reason_codes))
        return self


def canonical_payload(payload: dict[str, Any]) -> str:
    normalized = ensure_json(payload)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def evidence_hash_material(*, source_system: SourceSystem, source_ref: str | None, snapshot_id: str | None, subject_key: str, payload: dict[str, Any]) -> str:
    material = {
        "source_system": source_system.value,
        "source_ref": source_ref,
        "snapshot_id": snapshot_id,
        "subject_key": subject_key,
        "payload": json.loads(canonical_payload(payload)),
    }
    return hashlib.sha256(canonical_payload(material).encode("utf-8")).hexdigest()


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str | None = None
    evidence_type: EvidenceType
    source_system: SourceSystem
    source_ref: str | None = None
    subject_type: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    as_of: datetime
    available_at: datetime
    snapshot_id: str | None = None
    contract_version: str = Field(min_length=1)
    data_version: str | None = None
    payload: dict[str, Any]
    quality_status: EvidenceQuality
    confidence: float | None = Field(default=None, ge=0, le=1)
    freshness_seconds: int | None = Field(default=None, ge=0)
    lineage: list[EvidenceLineage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def model_copy(self, *, update=None, deep=False):
        values = self.model_dump(mode="python")
        values.update(update or {})
        return type(self).model_validate(values)

    @field_validator("as_of", "available_at")
    @classmethod
    def timestamps_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of and available_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_ownership_and_id(self) -> "Evidence":
        if (self.evidence_type == EvidenceType.DECISION_MEMORY) != (self.source_system == SourceSystem.STOCK_AGENT):
            raise ValueError("DECISION_MEMORY must be stock_agent-owned and stock_agent evidence must be DECISION_MEMORY")
        ensure_json(self.payload)
        digest = evidence_hash_material(source_system=self.source_system, source_ref=self.source_ref, snapshot_id=self.snapshot_id, subject_key=self.subject_key, payload=self.payload)
        canonical_id = f"ev-{self.source_system.value}-{self.evidence_type.value.lower()}-{digest[:32]}"
        if self.evidence_id is not None and self.evidence_id != canonical_id:
            raise ValueError("evidence_id does not match canonical evidence contents")
        object.__setattr__(self, "evidence_id", canonical_id)
        object.__setattr__(self, "payload", freeze(self.payload))
        object.__setattr__(self, "lineage", freeze(self.lineage))
        object.__setattr__(self, "warnings", freeze(self.warnings))
        return self


__all__ = ["DependencyStatus", "DependencyStatusValue", "Evidence", "EvidenceLineage", "EvidenceQuality", "EvidenceType", "SourceSystem", "canonical_payload", "evidence_hash_material"]

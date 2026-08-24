"""Deterministic evidence synthesis; this module never calls a model."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts.evidence import DependencyStatus, DependencyStatusValue, Evidence, EvidenceQuality, EvidenceType
from contracts.immutable import freeze


class EvidenceAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    evidence_id: str
    domain: str
    assessment: str
    reason: str | None = None


class EvidenceConflict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dimension: str
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str

    @model_validator(mode="after")
    def freeze_refs(self):
        object.__setattr__(self, "evidence_refs", freeze(self.evidence_refs))
        return self


class EvidenceCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    market: float = 0.0
    research: float = 0.0
    technical: float = 0.0
    factor: float = 0.0
    portfolio: float = 0.0
    risk: float = 0.0


class EvidenceSynthesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    supporting: list[EvidenceAssessment] = Field(default_factory=list)
    opposing: list[EvidenceAssessment] = Field(default_factory=list)
    uncertain: list[EvidenceAssessment] = Field(default_factory=list)
    key_conflicts: list[EvidenceConflict] = Field(default_factory=list)
    coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)
    missing_critical_evidence: list[str] = Field(default_factory=list)
    synthesis_hash: str = ""

    def model_copy(self, *, update=None, deep=False):
        values = self.model_dump(mode="python")
        values.update(update or {})
        return type(self).model_validate(values)

    @model_validator(mode="after")
    def hash_payload(self) -> "EvidenceSynthesis":
        payload = self.model_dump(mode="json", exclude={"synthesis_hash"})
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if self.synthesis_hash and self.synthesis_hash != digest:
            raise ValueError("synthesis_hash does not match synthesis")
        object.__setattr__(self, "synthesis_hash", digest)
        for field_name in ("supporting", "opposing", "uncertain", "key_conflicts", "missing_critical_evidence"):
            object.__setattr__(self, field_name, freeze(getattr(self, field_name)))
        return self


_DOMAIN_TYPES = {
    "market": {EvidenceType.MARKET_SNAPSHOT, EvidenceType.MARKET_REGIME, EvidenceType.MARKET_BREADTH, EvidenceType.SECTOR_STRENGTH},
    "research": {EvidenceType.KNOWLEDGE_CLAIM, EvidenceType.CATALYST, EvidenceType.RISK_EVENT, EvidenceType.VALUATION_FACT, EvidenceType.EARNINGS_FACT},
    "technical": {EvidenceType.TECHNICAL_SIGNAL, EvidenceType.TECHNICAL_PROFILE, EvidenceType.LIQUIDITY},
    "factor": {EvidenceType.FACTOR_SCORE, EvidenceType.FACTOR_SET, EvidenceType.FACTOR_RESEARCH_RESULT},
    "portfolio": {EvidenceType.PORTFOLIO_POSITION, EvidenceType.PORTFOLIO_EXPOSURE, EvidenceType.PORTFOLIO_RISK},
    "risk": {EvidenceType.RISK_EVENT, EvidenceType.PORTFOLIO_RISK},
}


def synthesize_evidence(evidence: list[Evidence], *, dependencies: list[DependencyStatus] | None = None, specialist_artifacts: list[dict[str, Any]] | None = None) -> EvidenceSynthesis:
    supporting: list[EvidenceAssessment] = []
    opposing: list[EvidenceAssessment] = []
    uncertain: list[EvidenceAssessment] = []
    stance_by_dimension: dict[str, dict[str, list[str]]] = {}
    for item in evidence:
        domains = [name for name, types in _DOMAIN_TYPES.items() if item.evidence_type in types]
        domain = domains[0] if domains else "unknown"
        # Decision memory is agent-owned context and must never increase external
        # domain coverage or appear as supporting/opposing fact.
        stance = str(item.payload.get("stance", item.payload.get("opinion", ""))).lower() if isinstance(item.payload, dict) else ""
        dimension = item.payload.get("conflict_dimension") if isinstance(item.payload, dict) else None
        if item.evidence_type != EvidenceType.DECISION_MEMORY and item.quality_status == EvidenceQuality.VERIFIED and dimension and stance:
            stance_by_dimension.setdefault(str(dimension), {}).setdefault(stance, []).append(item.evidence_id or "")
        target = uncertain if item.evidence_type == EvidenceType.DECISION_MEMORY or item.quality_status != EvidenceQuality.VERIFIED else opposing if stance in {"opposing", "against", "bearish", "negative"} else supporting
        if item.evidence_type == EvidenceType.DECISION_MEMORY:
            domain = "memory"
        target.append(EvidenceAssessment(evidence_id=item.evidence_id or "", domain=domain, assessment=item.quality_status.value, reason="decision memory is not external fact" if item.evidence_type == EvidenceType.DECISION_MEMORY else None))
        if item.evidence_type != EvidenceType.DECISION_MEMORY and item.quality_status == EvidenceQuality.VERIFIED:
            for extra_domain in domains[1:]:
                target.append(EvidenceAssessment(evidence_id=item.evidence_id or "", domain=extra_domain, assessment=item.quality_status.value, reason="multi-domain evidence"))
    verified = supporting + opposing
    coverage = EvidenceCoverage(**{domain: min(1.0, sum(item.domain == domain for item in verified) / 1.0) for domain in EvidenceCoverage.model_fields})
    missing = [domain for domain in EvidenceCoverage.model_fields if getattr(coverage, domain) == 0]
    for artifact in specialist_artifacts or []:
        values = artifact if isinstance(artifact, dict) else artifact.model_dump(mode="json")
        for unknown in values.get("unknowns") or []:
            if unknown not in missing:
                missing.append(str(unknown))
    if dependencies:
        for status in dependencies:
            if status.status in {DependencyStatusValue.UNAVAILABLE, DependencyStatusValue.STALE}:
                missing.append(f"{status.system.value.upper()}_{status.status.value}")
    conflicts = [EvidenceConflict(dimension=dimension, evidence_refs=[ref for refs in stances.values() for ref in refs], reason="explicit opposing stances") for dimension, stances in stance_by_dimension.items() if len(stances) > 1]
    return EvidenceSynthesis(supporting=supporting, opposing=opposing, uncertain=uncertain, key_conflicts=conflicts, coverage=coverage, missing_critical_evidence=missing)


__all__ = ["EvidenceAssessment", "EvidenceConflict", "EvidenceCoverage", "EvidenceSynthesis", "synthesize_evidence"]

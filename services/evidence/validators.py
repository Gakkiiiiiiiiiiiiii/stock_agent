from __future__ import annotations

from datetime import datetime

from contracts.evidence import Evidence, EvidenceQuality


def validate_evidence(evidence: Evidence) -> Evidence:
    if evidence.quality_status == EvidenceQuality.REJECTED:
        raise ValueError(f"rejected evidence: {evidence.evidence_id}")
    return evidence


def validate_available_at(evidence: Evidence, decision_time: datetime) -> Evidence:
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    if evidence.available_at > decision_time:
        raise ValueError(f"look-ahead evidence: {evidence.evidence_id}")
    return validate_evidence(evidence)


def freshness_quality(evidence: Evidence, *, decision_time: datetime, max_age_seconds: int | None = None) -> EvidenceQuality:
    if evidence.quality_status in {EvidenceQuality.REJECTED, EvidenceQuality.STALE}:
        return evidence.quality_status
    if max_age_seconds is not None and (decision_time - evidence.as_of).total_seconds() > max_age_seconds:
        return EvidenceQuality.STALE
    return evidence.quality_status

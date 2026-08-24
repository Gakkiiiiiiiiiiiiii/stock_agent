from __future__ import annotations

from datetime import datetime
from typing import Any

from contracts.evidence import Evidence, EvidenceLineage, EvidenceQuality, EvidenceType, SourceSystem


class PortfolioEvidenceAdapter:
    source_system = SourceSystem.QUANT

    def to_evidence(self, payload: dict[str, Any], *, evidence_type: EvidenceType = EvidenceType.PORTFOLIO_POSITION, subject_key: str = "portfolio", subject_type: str = "portfolio", as_of: datetime, available_at: datetime, contract_version: str = "market-data.v1", quality_status: EvidenceQuality = EvidenceQuality.PARTIAL, confidence: float | None = None, freshness_seconds: int | None = None, data_version: str | None = None, snapshot_id: str | None = None, source_ref: str = "quant") -> Evidence:
        return Evidence(evidence_type=evidence_type, source_system=self.source_system, source_ref=source_ref, subject_type=subject_type, subject_key=subject_key, as_of=as_of, available_at=available_at, snapshot_id=snapshot_id, contract_version=contract_version, data_version=data_version, payload=payload, quality_status=quality_status, confidence=confidence, freshness_seconds=freshness_seconds, lineage=[EvidenceLineage(source_ref=source_ref)])

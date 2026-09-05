"""Deterministic, read-only evidence source for explicit CI smoke mode.

This adapter is deliberately selected only by the composition root when
``STOCK_AGENT_DETERMINISTIC_FIXTURE=1``.  It never contacts a producer and
always returns complete, point-in-time evidence with stable identities.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from contracts.evidence import (
    DependencyStatus,
    DependencyStatusValue,
    Evidence,
    EvidenceQuality,
    EvidenceType,
    SourceSystem,
)


class DeterministicFixtureEvidenceGateway:
    """Non-production evidence gateway used by the Kubernetes golden path."""

    def collect(
        self,
        requests: list[dict[str, Any]],
        *,
        decision_time: datetime,
        trace: Any = None,
        trace_context: dict[str, Any] | None = None,
    ) -> tuple[list[Evidence], list[DependencyStatus]]:
        del requests, trace, trace_context
        rows = (
            (EvidenceType.MARKET_SNAPSHOT, SourceSystem.QUANT, "fixture-market", "market", {"snapshot_id": "fixture-market", "close": 100.0}, "market-data.v1"),
            (EvidenceType.MARKET_REGIME, SourceSystem.QUANT, "fixture-market", "market", {"regime": "neutral"}, "market-data.v1"),
            (EvidenceType.SECTOR_STRENGTH, SourceSystem.QUANT, "fixture-market", "market", {"items": []}, "market-data.v1"),
            (EvidenceType.PORTFOLIO_POSITION, SourceSystem.QUANT, "fixture-portfolio", "portfolio", {"positions": [], "gross_exposure": 0.0, "net_exposure": 0.0}, "market-data.v1"),
            (EvidenceType.PORTFOLIO_RISK, SourceSystem.QUANT, "fixture-portfolio", "portfolio", {"risk_score": 0.0, "veto": False}, "market-data.v1"),
            (EvidenceType.FACTOR_SCORE, SourceSystem.FACTOR, "fixture-factor", "factor", {"factor_artifact_id": "fixture-factor-artifact", "score": 0.0}, "factor.v1"),
            (EvidenceType.KNOWLEDGE_CLAIM, SourceSystem.CONTENT, "fixture-content", "content", {"content_snapshot_id": "fixture-content", "claim_id": "fixture-claim", "claim": "deterministic fixture"}, "content.v1"),
        )
        evidence = [
            Evidence(
                evidence_type=evidence_type,
                source_system=system,
                source_ref=f"{system.value}:deterministic-fixture:{evidence_type.value.lower()}",
                subject_type=subject,
                subject_key=subject,
                as_of=decision_time,
                available_at=decision_time,
                snapshot_id=snapshot_id,
                contract_version=contract,
                payload=payload,
                quality_status=EvidenceQuality.VERIFIED,
                confidence=1.0,
            )
            for evidence_type, system, snapshot_id, subject, payload, contract in rows
        ]
        statuses = [
            DependencyStatus(
                system=system,
                status=DependencyStatusValue.OK,
                checked_at=decision_time,
                contract_version=contract,
                service_version="deterministic-fixture.v1",
                snapshot_id=snapshot_id,
            )
            for system, contract, snapshot_id in (
                (SourceSystem.QUANT, "market-data.v1", "fixture-market"),
                (SourceSystem.FACTOR, "factor.v1", "fixture-factor"),
                (SourceSystem.CONTENT, "content.v1", "fixture-content"),
            )
        ]
        return evidence, statuses


__all__ = ["DeterministicFixtureEvidenceGateway"]

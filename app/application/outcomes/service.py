"""Single durable outcome-evaluation application service.

The HTTP and job-worker entrypoints use this service so provider calls are
protected by the same persistent lease/fencing and result identity rules.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.application.outcomes.lease_repository import OutcomeLeaseRepository
from workers.outcome_worker import OutcomeWorker


class OutcomeEvaluationService:
    def __init__(
        self,
        *,
        provider: Any,
        worker: OutcomeWorker | None = None,
        leases: OutcomeLeaseRepository | None = None,
    ) -> None:
        self.provider = provider
        self.worker = worker or OutcomeWorker(leases or OutcomeLeaseRepository(persistent=True))

    def refresh(
        self,
        *,
        decision_id: str,
        decision_snapshot_id: str,
        horizon: str,
        measured_at: datetime,
        owner_id: str,
    ) -> dict[str, Any]:
        def calculate() -> dict[str, Any]:
            fetch = (
                getattr(self.provider, "fetch_outcome", None)
                or getattr(self.provider, "refresh", None)
                or getattr(self.provider, "get_outcome", None)
            )
            if fetch is None:
                raise ValueError("quant outcome provider lacks read-only fetch_outcome")
            raw = fetch(decision_id=decision_id, horizon=horizon, measured_at=measured_at)
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump(mode="json")
            if not isinstance(raw, dict):
                raise ValueError("quant outcome provider returned a non-object")  # noqa: TRY004 - stable provider error code
            value = dict(raw)
            # Freeze the market-result identity at evaluation time.  A later
            # retry returns this durable result instead of re-reading current
            # market data under the same decision snapshot.
            snapshot_id = value.get("market_snapshot_id") or f"market-result:{decision_snapshot_id}:{horizon}:{measured_at.isoformat()}"
            snapshot_hash = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
            attribution = dict(value.get("attribution") or {})
            attribution.setdefault("market_result_snapshot_id", snapshot_id)
            attribution.setdefault("market_result_snapshot_hash", snapshot_hash)
            attribution.setdefault("observation_window", {"horizon": horizon, "measured_at": measured_at.isoformat()})
            attribution.setdefault("calculation_version", "outcome-evaluation.v2")
            value["attribution"] = attribution
            return value

        result = self.worker.evaluate(
            decision_snapshot_id=decision_snapshot_id,
            owner_id=owner_id,
            calculator=calculate,
        )
        if result is None:
            raise RuntimeError("OUTCOME_LEASE_BUSY")
        return result

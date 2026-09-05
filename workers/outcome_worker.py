"""Outcome worker entrypoint using the shared lease service."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.application.outcomes.lease_repository import OutcomeLeaseRepository


class OutcomeWorker:
    def __init__(self, leases: OutcomeLeaseRepository | None = None):
        # Worker processes share the SQL lease/fencing record. Tests and
        # embedded callers can still inject the deterministic in-memory port.
        self.leases = leases or OutcomeLeaseRepository(persistent=True)

    def evaluate(self, *, decision_snapshot_id: str, owner_id: str, calculator: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
        prior = self.leases.result(decision_snapshot_id)
        if prior is not None:
            return prior
        lease = self.leases.claim(decision_snapshot_id, owner_id)
        if lease is None:
            return None
        return self.leases.complete(lease, calculator())

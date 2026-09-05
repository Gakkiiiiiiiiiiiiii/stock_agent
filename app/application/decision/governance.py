from __future__ import annotations

from typing import Any


class GovernanceService:
    def evaluate(self, result: dict[str, Any], *, policy_version: str) -> dict[str, Any]:
        # Governance is an output of the persisted policy/calculation stage;
        # this adapter must never manufacture approval merely because a run
        # reached this service.  Missing approval is fail-closed.
        approved = result.get("approved") is True
        return {"approved": approved, "policy_version": policy_version, "formal_result": result}

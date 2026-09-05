from __future__ import annotations

from typing import Any


class FormalDecisionCalculator:
    def calculate(self, bundle: dict[str, Any], specialists: dict[str, Any] | None = None) -> dict[str, Any]:
        # Formal default is deterministic HOLD; model suggestions cannot make it
        # executable without governance/finalization.
        return {"status": "HOLD", "execution_eligible": False, "bundle_hash": bundle.get("bundle_hash"), "specialists": specialists or {}}

from __future__ import annotations

from typing import Any

from app.application.decision.assembler import EvidenceBundleAssembler
from app.application.decision.formal_calculator import FormalDecisionCalculator
from app.application.decision.governance import GovernanceService
from app.application.decision.specialists import NoopSpecialistRunner


class DecisionApplicationService:
    """Composition-root service for the split pipeline."""
    def __init__(self, assembler: Any | None = None, specialists: Any | None = None, calculator: Any | None = None, governance: Any | None = None) -> None:
        self.assembler = assembler or EvidenceBundleAssembler()
        self.specialists = specialists or NoopSpecialistRunner()
        self.calculator = calculator or FormalDecisionCalculator()
        self.governance = governance or GovernanceService()

    def calculate(self, *, market: Any, factor: Any, content: Any, lineage: dict[str, Any], policy_version: str) -> dict[str, Any]:
        bundle = self.assembler.freeze(market=market, factor=factor, content=content, lineage=lineage)
        specialists = self.specialists.run(bundle)
        result = self.calculator.calculate(bundle, specialists)
        return self.governance.evaluate(result, policy_version=policy_version) | {"bundle": bundle}

    def calculate_from_frozen(self, *, bundle: dict[str, Any], policy_version: str) -> dict[str, Any]:
        """Calculate strictly from an already frozen bundle; no providers are accepted."""
        expected = self.assembler.freeze(
            market=bundle.get("market"), factor=bundle.get("factor"),
            content=bundle.get("content"), lineage=bundle.get("lineage") or {},
        )
        if expected.get("bundle_hash") != bundle.get("bundle_hash"):
            raise ValueError("FROZEN_BUNDLE_HASH_MISMATCH")
        specialists = self.specialists.run(bundle)
        result = self.calculator.calculate(bundle, specialists)
        return self.governance.evaluate(result, policy_version=policy_version) | {"bundle": bundle}

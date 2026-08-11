"""Single, persisted gate for moving a factor into paper trading.

Keeping this decision separate from the miner prevents a caller from treating a
successful walk-forward run as an implicit production approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromotionGateResult:
    passed: bool
    metrics: dict[str, Any]
    reject_reasons: list[str]

    def model_dump(self) -> dict:
        return {"passed": self.passed, "metrics": self.metrics, "reject_reasons": self.reject_reasons}


def evaluate_promotion_gate(
    *,
    walkforward: dict | None,
    statistics: dict | None,
    min_window_pass_ratio: float = 0.60,
) -> PromotionGateResult:
    """Require both purged walk-forward and multiple-testing validation."""
    walkforward, statistics = walkforward or {}, statistics or {}
    reasons: list[str] = []
    if not walkforward.get("passed"):
        reasons.append("WALKFORWARD_FAILED")
    if float(walkforward.get("window_pass_ratio", 0.0)) < min_window_pass_ratio:
        reasons.append("WALKFORWARD_COVERAGE_FAILED")
    if not statistics.get("passed"):
        reasons.append("STATISTICAL_VALIDATION_FAILED")
    return PromotionGateResult(
        passed=not reasons,
        metrics={"walkforward": walkforward, "statistics": statistics},
        reject_reasons=reasons,
    )

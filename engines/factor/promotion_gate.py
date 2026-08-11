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
    diagnostics: dict | None = None,
    exposure: dict | None = None,
    capacity: dict | None = None,
    min_window_pass_ratio: float = 0.60,
    min_capacity_proxy: float = 1.0,
    max_abs_liquidity_exposure: float = 0.80,
    gate_version: str = "promotion_gate_v2",
    statistical_validation_version: str = "factor_statistics_v1",
    data_snapshot_id: str | None = None,
) -> PromotionGateResult:
    """Deterministic promotion gate with explicit diagnostic provenance."""
    walkforward, statistics = walkforward or {}, statistics or {}
    diagnostics, exposure, capacity = diagnostics or {}, exposure or {}, capacity or {}
    reasons: list[str] = []
    if not walkforward.get("passed"):
        reasons.append("WALKFORWARD_FAILED")
    if float(walkforward.get("window_pass_ratio", 0.0)) < min_window_pass_ratio:
        reasons.append("WALKFORWARD_COVERAGE_FAILED")
    if not statistics.get("passed"):
        reasons.append("STATISTICAL_VALIDATION_FAILED")
    if not diagnostics:
        reasons.append("DIAGNOSTICS_MISSING")
    else:
        decay = diagnostics.get("ic_decay") or {}
        day_1 = decay.get(1, decay.get("1"))
        day_5 = decay.get(5, decay.get("5"))
        if day_1 is not None and day_5 is not None and abs(float(day_1)) > 1e-8 and abs(float(day_5)) <= 1e-8:
            reasons.append("IC_DECAY_FAILED")
    if not exposure:
        reasons.append("EXPOSURE_MISSING")
    elif exposure.get("liquidity_exposure") is not None and abs(float(exposure["liquidity_exposure"])) > max_abs_liquidity_exposure:
        reasons.append("LIQUIDITY_EXPOSURE_FAILED")
    if not capacity:
        reasons.append("CAPACITY_MISSING")
    elif float(capacity.get("daily_notional_capacity_proxy", 0.0)) < min_capacity_proxy:
        reasons.append("CAPACITY_FAILED")
    return PromotionGateResult(
        passed=not reasons,
        metrics={
            "walkforward": walkforward,
            "statistics": statistics,
            "diagnostics": diagnostics,
            "exposure": exposure,
            "capacity": capacity,
            "promotion_gate_version": gate_version,
            "statistical_validation_version": statistical_validation_version,
            "data_snapshot_id": data_snapshot_id,
        },
        reject_reasons=reasons,
    )

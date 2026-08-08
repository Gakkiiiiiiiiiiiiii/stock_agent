from __future__ import annotations


DEFAULT_TOLERANCE = 0.03


def compare_to_baseline(current: dict, baseline: dict, tolerance: float = DEFAULT_TOLERANCE) -> list[str]:
    """Return regression violations; callers decide whether to fail CI."""
    violations: list[str] = []
    for metric in ("recall_at_10", "ndcg_at_10", "temporal_accuracy", "conflict_accuracy", "memory_type_recall", "regime_conditioned_recall"):
        if metric in current and metric in baseline and float(current[metric]) < float(baseline[metric]) - tolerance:
            violations.append(f"REGRESSION:{metric}")
    leakage = "expired_knowledge_leakage_rate"
    if leakage in current and leakage in baseline and float(current[leakage]) > float(baseline[leakage]):
        violations.append(f"REGRESSION:{leakage}")
    return violations

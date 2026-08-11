from __future__ import annotations


DEFAULT_TOLERANCE = 0.03


def compare_to_baseline(current: dict, baseline: dict, tolerance: float = DEFAULT_TOLERANCE) -> list[str]:
    """Return regression violations; callers decide whether to fail CI.

    Newer v2 metrics are compared only when the baseline file carries them;
    absent keys are skipped so baseline_v1.json stays valid.
    """
    violations: list[str] = []
    higher_is_better = (
        "recall_at_10",
        "ndcg_at_10",
        "temporal_accuracy",
        "conflict_accuracy",
        "memory_type_recall",
        "regime_conditioned_recall",
        # schema v2 (opt-in: skipped when the baseline lacks them)
        "temporal_precision",
        "conflict_resolution_accuracy",
        "source_diversity",
    )
    for metric in higher_is_better:
        if metric in current and metric in baseline and float(current[metric]) < float(baseline[metric]) - tolerance:
            violations.append(f"REGRESSION:{metric}")
    for leakage in ("expired_knowledge_leakage_rate", "expired_context_rate"):
        if leakage in current and leakage in baseline and float(current[leakage]) > float(baseline[leakage]):
            violations.append(f"REGRESSION:{leakage}")
    return violations

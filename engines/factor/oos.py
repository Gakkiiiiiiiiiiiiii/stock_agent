from __future__ import annotations

import numpy as np

from engines.factor.fitness import evaluate_factor


def evaluate_oos_splits(
    factor_panel: np.ndarray,
    closes: np.ndarray,
    horizon: int = 5,
    train_ratio: float = 0.6,
    validation_ratio: float = 0.2,
) -> dict:
    n_days = factor_panel.shape[1]
    train_end = max(1, int(n_days * train_ratio))
    val_end = max(train_end + 1, int(n_days * (train_ratio + validation_ratio)))
    val_end = min(val_end, n_days)
    splits = {
        "train": (0, train_end),
        "validation": (train_end, val_end),
        "test": (val_end, n_days),
    }
    results = {}
    for name, (start, end) in splits.items():
        if end - start <= horizon + 2:
            results[name] = {"passed": False, "warning": "split too short"}
            continue
        window = end - start
        sub_factor = factor_panel[:, :end]
        sub_closes = closes[:, :end]
        results[name] = evaluate_factor(sub_factor, sub_closes, horizon=horizon, eval_window=window)
    passed = bool(results.get("validation", {}).get("passed")) and bool(results.get("test", {}).get("passed"))
    return {"splits": results, "passed": passed, "failure_reasons": _failure_reasons(results)}


def _failure_reasons(results: dict) -> list[str]:
    reasons = []
    for name, metrics in results.items():
        if not metrics.get("passed"):
            reasons.append(f"{name}_failed")
    return reasons

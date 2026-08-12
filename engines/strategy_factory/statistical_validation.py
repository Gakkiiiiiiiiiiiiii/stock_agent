"""Strategy promotion statistics independent of the remote Factor Engine."""
from __future__ import annotations

from itertools import combinations
from math import erf, sqrt

import numpy as np


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> dict:
    values = np.asarray(p_values, dtype=float)
    if values.ndim != 1 or np.any((values < 0) | (values > 1)):
        raise ValueError("p_values must be values in [0, 1]")
    count = len(values)
    if not count:
        return {"adjusted_p_values": [], "rejected": [], "fdr_pass": False}
    order = np.argsort(values, kind="mergesort")
    ordered = values[order]
    adjusted = np.minimum.accumulate((ordered * count / np.arange(1, count + 1))[::-1])[::-1]
    expanded = np.empty(count, dtype=float)
    expanded[order] = np.minimum(adjusted, 1)
    rejected = expanded <= alpha
    return {
        "adjusted_p_values": [round(float(item), 10) for item in expanded],
        "rejected": [bool(item) for item in rejected],
        "fdr_pass": bool(np.any(rejected)),
    }


def deflated_sharpe_ratio(
    sharpe: float, observations: int, trials: int = 1, skewness: float = 0.0, kurtosis: float = 3.0
) -> float:
    if observations <= 1:
        return 0.0
    variance = max(1e-12, 1 - skewness * sharpe + ((kurtosis - 1) / 4) * sharpe * sharpe)
    selection_penalty = sqrt(2 * np.log(max(1, trials))) / sqrt(max(1, observations))
    z = (sharpe - selection_penalty) * sqrt(observations - 1) / sqrt(variance)
    return round(float(0.5 * (1 + erf(z / sqrt(2)))), 8)


def probability_of_backtest_overfitting(returns_by_trial: np.ndarray, partitions: int = 8) -> float:
    values = np.asarray(returns_by_trial, dtype=float)
    if values.ndim != 2 or values.shape[0] < 2 or values.shape[1] < 4:
        return 1.0
    chunks = [item for item in np.array_split(np.arange(values.shape[1]), min(max(2, partitions), values.shape[1])) if len(item)]
    failures = total = 0
    for subset_size in range(1, len(chunks) // 2 + 1):
        for combo in combinations(range(len(chunks)), subset_size):
            ins = np.concatenate([chunks[index] for index in combo])
            oos = np.concatenate([chunks[index] for index in range(len(chunks)) if index not in combo])
            if not len(oos):
                continue
            selected = int(np.nanargmax(np.nanmean(values[:, ins], axis=1)))
            rank = np.sum(np.nanmean(values[:, oos], axis=1) <= np.nanmean(values[selected, oos]))
            failures += int(rank <= values.shape[0] / 2)
            total += 1
    return round(failures / total, 8) if total else 1.0


def validate_strategy_statistics(
    p_values: list[float],
    sharpe: float,
    observations: int,
    trials: int,
    returns_by_trial: np.ndarray,
    alpha: float = 0.05,
    min_deflated_sharpe: float = 0.5,
    max_pbo: float = 0.35,
) -> dict:
    fdr = benjamini_hochberg(p_values, alpha)
    dsr = deflated_sharpe_ratio(sharpe, observations, trials)
    pbo = probability_of_backtest_overfitting(returns_by_trial)
    reasons = []
    if not fdr["fdr_pass"]:
        reasons.append("FDR_FAILED")
    if dsr < min_deflated_sharpe:
        reasons.append("DEFLATED_SHARPE_FAILED")
    if pbo > max_pbo:
        reasons.append("PBO_FAILED")
    return {
        "fdr_pass": fdr["fdr_pass"],
        "adjusted_p_value": min(fdr["adjusted_p_values"], default=1.0),
        "deflated_sharpe": dsr,
        "pbo": pbo,
        "passed": not reasons,
        "reject_reasons": reasons,
    }

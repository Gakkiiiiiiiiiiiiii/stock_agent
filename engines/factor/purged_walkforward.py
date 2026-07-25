from __future__ import annotations

import numpy as np

from engines.factor.fitness import evaluate_factor
from engines.factor.purged_split import build_purged_windows


def run_purged_walkforward(factor_panel: np.ndarray, closes: np.ndarray, horizon: int = 5, embargo: int | None = None) -> dict:
    windows = []
    for window in build_purged_windows(factor_panel.shape[1], horizon=horizon, embargo=embargo):
        start, end = window.test
        metrics = evaluate_factor(factor_panel[:, :end], closes[:, :end], horizon=horizon, eval_window=end - start)
        windows.append({"train": window.train, "validation": window.validation, "test": window.test, "metrics": metrics, "passed": bool(metrics.get("passed"))})
    rank_ics = [float(item["metrics"].get("rank_ic") or 0.0) for item in windows]
    excess = [float(item["metrics"].get("topk_excess_return", item["metrics"].get("topk_annual_return") or 0.0)) for item in windows]
    positive = [value > 0 for value in rank_ics]
    passed = bool(windows) and (sum(positive) / len(positive)) >= 0.6 and min(rank_ics) > -0.02
    return {
        "windows": windows,
        "mean_rank_ic": round(float(np.mean(rank_ics)), 6) if rank_ics else 0.0,
        "min_rank_ic": round(float(np.min(rank_ics)), 6) if rank_ics else 0.0,
        "positive_window_ratio": round(sum(positive) / len(positive), 6) if positive else 0.0,
        "oos_excess_return": round(float(np.mean(excess)), 6) if excess else 0.0,
        "passed": passed,
    }

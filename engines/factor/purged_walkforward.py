from __future__ import annotations

import numpy as np

from engines.factor.fitness import evaluate_factor
from engines.factor.purged_split import build_purged_windows


def run_purged_walkforward(factor_panel: np.ndarray, closes: np.ndarray, horizon: int = 5, embargo: int | None = None) -> dict:
    windows = []
    for window in build_purged_windows(factor_panel.shape[1], horizon=horizon, embargo=embargo):
        start, end = window.test
        available_end = min(factor_panel.shape[1], end + horizon)
        if available_end < end + horizon:
            metrics = {"passed": False, "warning": "insufficient future horizon for test window"}
        else:
            test_factor = factor_panel[:, :available_end].copy()
            test_factor[:, :start] = np.nan
            test_factor[:, end:] = np.nan
            metrics = evaluate_factor(test_factor, closes[:, :available_end], horizon=horizon, eval_window=available_end - start)
        windows.append({"train": window.train, "validation": window.validation, "test": window.test, "metrics": metrics, "passed": bool(metrics.get("passed"))})
    rank_ics = [float(item["metrics"].get("rank_ic") or 0.0) for item in windows]
    excess = [float(item["metrics"].get("topk_excess_annual_return", item["metrics"].get("topk_excess_return") or 0.0)) for item in windows]
    positive = [value > 0 for value in rank_ics]
    window_pass_ratio = sum(1 for item in windows if item.get("passed")) / len(windows) if windows else 0.0
    positive_rank_ic_ratio = sum(positive) / len(positive) if positive else 0.0
    mean_excess = float(np.mean(excess)) if excess else 0.0
    passed = (
        bool(windows)
        and window_pass_ratio >= 0.6
        and positive_rank_ic_ratio >= 0.6
        and mean_excess > 0
        and min(rank_ics) > -0.02
    )
    return {
        "method": "purged_walkforward",
        "windows": windows,
        "mean_rank_ic": round(float(np.mean(rank_ics)), 6) if rank_ics else 0.0,
        "min_rank_ic": round(float(np.min(rank_ics)), 6) if rank_ics else 0.0,
        "window_pass_ratio": round(window_pass_ratio, 6),
        "positive_window_ratio": round(positive_rank_ic_ratio, 6),
        "oos_excess_return": round(mean_excess, 6),
        "passed": passed,
    }

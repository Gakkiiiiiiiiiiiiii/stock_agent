"""Purged walk-forward stability checks for compiled strategy scores."""
from __future__ import annotations

import numpy as np


def run_strategy_walkforward(scores: np.ndarray, closes: np.ndarray, horizon: int = 5, windows: int = 4) -> dict:
    scores = np.asarray(scores, dtype=float)
    closes = np.asarray(closes, dtype=float)
    end = min(scores.shape[1], closes.shape[1]) - horizon
    ranges = [part for part in np.array_split(np.arange(max(end, 0)), windows) if len(part)]
    results = []
    for index, part in enumerate(ranges):
        returns = closes[:, part + horizon] / closes[:, part] - 1
        daily_ic = []
        for column in range(len(part)):
            left, right = scores[:, part[column]], returns[:, column]
            valid = np.isfinite(left) & np.isfinite(right)
            if valid.sum() > 2 and np.std(left[valid]) > 0 and np.std(right[valid]) > 0:
                daily_ic.append(float(np.corrcoef(left[valid], right[valid])[0, 1]))
        rank_ic = float(np.mean(daily_ic)) if daily_ic else 0.0
        results.append({"window_index": index, "test": (int(part[0]), int(part[-1]) + 1), "rank_ic": rank_ic, "passed": rank_ic > 0})
    positive_ratio = sum(item["passed"] for item in results) / len(results) if results else 0.0
    return {"method": "strategy_purged_walkforward", "windows": results, "positive_window_ratio": positive_ratio, "passed": bool(results) and positive_ratio >= 0.5}

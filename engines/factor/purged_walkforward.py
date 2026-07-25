from __future__ import annotations

import numpy as np

from engines.factor.fitness import evaluate_factor_range
from financial_agent.research_config import get_research_config


def run_purged_walkforward(
    factor_panel: np.ndarray,
    closes: np.ndarray,
    eval_start: int | None = None,
    eval_end: int | None = None,
    horizon: int = 5,
    n_windows: int | None = None,
    embargo: int | None = None,
) -> dict:
    config = get_research_config().purged_walkforward
    eval_start = 0 if eval_start is None else max(0, int(eval_start))
    eval_end = min(factor_panel.shape[1] - horizon, int(eval_end) if eval_end is not None else factor_panel.shape[1] - horizon)
    n_windows = n_windows or config.n_windows
    embargo = config.embargo_days if embargo is None else embargo
    windows = []
    for index, (start, end) in enumerate(_build_eval_windows(eval_start, eval_end, n_windows=n_windows, embargo=embargo)):
        if end + horizon > factor_panel.shape[1]:
            metrics = {"passed": False, "warning": "insufficient future horizon for test window"}
        else:
            metrics = evaluate_factor_range(
                factor_panel,
                closes,
                eval_start=start,
                eval_end=end,
                horizon=horizon,
            )
        history_range = (0, max(eval_start, start - (embargo or 0)))
        embargo_range = (history_range[1], start)
        test_range = (start, end)
        windows.append({
            "history_range": history_range,
            "embargo_range": embargo_range,
            "test_range": test_range,
            "train": history_range,
            "validation": embargo_range,
            "test": test_range,
            "window_index": index,
            "metrics": metrics,
            "passed": bool(metrics.get("passed")),
        })
    rank_ics = [float(item["metrics"].get("rank_ic") or 0.0) for item in windows]
    excess = [float(item["metrics"].get("topk_excess_annual_return", item["metrics"].get("topk_excess_return") or 0.0)) for item in windows]
    positive = [value > 0 for value in rank_ics]
    window_pass_ratio = sum(1 for item in windows if item.get("passed")) / len(windows) if windows else 0.0
    positive_rank_ic_ratio = sum(positive) / len(positive) if positive else 0.0
    mean_excess = float(np.mean(excess)) if excess else 0.0
    passed = (
        bool(windows)
        and window_pass_ratio >= config.min_window_pass_ratio
        and positive_rank_ic_ratio >= config.min_positive_rank_ic_ratio
        and mean_excess > config.min_oos_excess_return
        and min(rank_ics) > config.min_rank_ic_floor
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


def _build_eval_windows(eval_start: int, eval_end: int, n_windows: int, embargo: int | None) -> list[tuple[int, int]]:
    count = max(int(n_windows), 1)
    gap = max(int(embargo or 0), 0)
    total = eval_end - eval_start
    usable = total - gap * (count - 1)
    if usable < count:
        return []
    base = usable // count
    remainder = usable % count
    windows: list[tuple[int, int]] = []
    cursor = eval_start
    for index in range(count):
        start = cursor
        width = base + (1 if index < remainder else 0)
        end = min(eval_end, start + width)
        if end > start:
            windows.append((start, end))
        cursor = end + gap
        if cursor >= eval_end:
            break
    return windows

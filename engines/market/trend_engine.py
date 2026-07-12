from __future__ import annotations


def compute_trend_score(index_return_5d: float, index_return_20d: float) -> float:
    raw = 0.5 + index_return_5d * 2 + index_return_20d
    return round(max(0.0, min(1.0, raw)), 4)


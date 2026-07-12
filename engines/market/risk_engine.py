from __future__ import annotations


def compute_drawdown_risk(index_drawdown_20d: float, limit_down_count: int) -> float:
    score = min(abs(index_drawdown_20d) * 4, 1.0) * 0.7 + min(limit_down_count / 30, 1.0) * 0.3
    return round(max(0.0, min(1.0, score)), 4)


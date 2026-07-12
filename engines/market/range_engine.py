from __future__ import annotations


def compute_range_score(index_volatility: float, breadth: float) -> float:
    volatility_component = max(0.0, 1 - abs(index_volatility - 0.02) * 10)
    breadth_component = max(0.0, 1 - abs(breadth - 0.5) * 2)
    return round(max(0.0, min(1.0, volatility_component * 0.5 + breadth_component * 0.5)), 4)


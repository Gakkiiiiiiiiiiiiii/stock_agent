from __future__ import annotations


def score_signal(pattern: str, base_score: float, route: dict) -> float:
    weight = route["preferred_strategies"].get(pattern, 0.05)
    return round(max(0.0, min(100.0, base_score * (0.7 + weight))), 2)


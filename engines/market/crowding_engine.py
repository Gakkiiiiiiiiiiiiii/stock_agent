from __future__ import annotations


def compute_crowding_score(top_theme_strength: float, limit_up_count: int) -> float:
    score = top_theme_strength / 100 * 0.7 + min(limit_up_count / 50, 1) * 0.3
    return round(max(0.0, min(1.0, score)), 4)


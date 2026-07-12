from __future__ import annotations


def compute_rotation_score(top_theme_strength: float, breadth: float) -> float:
    dispersion = 1 - abs(top_theme_strength / 100 - breadth)
    return round(max(0.0, min(1.0, dispersion)), 4)


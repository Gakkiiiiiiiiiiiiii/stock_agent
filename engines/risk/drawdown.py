from __future__ import annotations

from collections.abc import Sequence


def max_drawdown(values: Sequence[float]) -> float:
    peak = None
    worst = 0.0
    for value in values:
        peak = value if peak is None else max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1)
    return round(abs(worst), 4)


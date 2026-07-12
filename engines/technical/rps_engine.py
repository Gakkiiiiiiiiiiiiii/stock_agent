from __future__ import annotations

from collections.abc import Mapping, Sequence


def period_return(closes: Sequence[float], window: int) -> float | None:
    if len(closes) <= window or closes[-window - 1] == 0:
        return None
    return closes[-1] / closes[-window - 1] - 1


def calculate_rps(close_map: Mapping[str, Sequence[float]], window: int) -> dict[str, float | None]:
    returns = {symbol: period_return(closes, window) for symbol, closes in close_map.items()}
    valid = sorted((value for value in returns.values() if value is not None))
    if not valid:
        return {symbol: None for symbol in close_map}
    result: dict[str, float | None] = {}
    for symbol, value in returns.items():
        if value is None:
            result[symbol] = None
            continue
        below_or_equal = sum(1 for item in valid if item <= value)
        result[symbol] = round(below_or_equal / len(valid) * 100, 2)
    return result


def in_rps_pool(rps50: float | None = None, rps120: float | None = None, close: float | None = None, ltl_value: float | None = None) -> bool:
    strength_ok = (rps50 is not None and rps50 >= 85) or (rps120 is not None and rps120 >= 85)
    trend_ok = close is None or ltl_value is None or close >= ltl_value
    return strength_ok and trend_ok


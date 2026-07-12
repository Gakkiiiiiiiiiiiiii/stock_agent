from __future__ import annotations

from collections.abc import Sequence


def _none_prefix(values: list[float | None], window: int) -> list[float | None]:
    return [None if i < window - 1 else values[i] for i in range(len(values))]


def ma(values: Sequence[float], window: int) -> list[float | None]:
    if window <= 0:
        raise ValueError("window must be positive")
    result: list[float | None] = []
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= window:
            running -= values[i - window]
        result.append(running / window if i >= window - 1 else None)
    return result


def ema(values: Sequence[float], span: int) -> list[float]:
    if span <= 0:
        raise ValueError("span must be positive")
    if not values:
        return []
    alpha = 2 / (span + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, list[float]]:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    dif = [a - b for a, b in zip(ema_fast, ema_slow)]
    dea = ema(dif, signal)
    hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return {"dif": dif, "dea": dea, "macd": hist}


def kdj(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> dict[str, list[float | None]]:
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows and closes must have the same length")
    k_raw: list[float | None] = []
    k_values: list[float | None] = []
    d_values: list[float | None] = []
    last_k = 50.0
    last_d = 50.0
    for i, close in enumerate(closes):
        if i < n - 1:
            k_raw.append(None)
            k_values.append(None)
            d_values.append(None)
            continue
        high_n = max(highs[i - n + 1 : i + 1])
        low_n = min(lows[i - n + 1 : i + 1])
        rsv = 50.0 if high_n == low_n else (close - low_n) / (high_n - low_n) * 100
        last_k = (m1 - 1) / m1 * last_k + rsv / m1
        last_d = (m2 - 1) / m2 * last_d + last_k / m2
        k_raw.append(rsv)
        k_values.append(last_k)
        d_values.append(last_d)
    j_values = [None if k is None or d is None else 3 * k - 2 * d for k, d in zip(k_values, d_values)]
    return {"rsv": k_raw, "k": k_values, "d": d_values, "j": j_values}


def stl(closes: Sequence[float]) -> list[float]:
    return ema(ema(closes, 10), 10)


def ltl(closes: Sequence[float]) -> list[float | None]:
    ma14 = ma(closes, 14)
    ma28 = ma(closes, 28)
    ma57 = ma(closes, 57)
    ma114 = ma(closes, 114)
    result: list[float | None] = []
    for values in zip(ma14, ma28, ma57, ma114):
        result.append(None if any(v is None for v in values) else sum(v for v in values if v is not None) / 4)
    return result


def rolling_ratio(values: Sequence[float], window: int) -> list[float | None]:
    avg = ma(values, window)
    return [None if a in (None, 0) else value / a for value, a in zip(values, avg)]


def calc_all(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], volumes: Sequence[float]) -> dict[str, list[float | None] | list[float]]:
    macd_values = macd(closes)
    kdj_values = kdj(highs, lows, closes)
    result: dict[str, list[float | None] | list[float]] = {
        "ma5": ma(closes, 5),
        "ma10": ma(closes, 10),
        "ma20": ma(closes, 20),
        "ma60": ma(closes, 60),
        "ma120": ma(closes, 120),
        "ma240": ma(closes, 240),
        "stl": stl(closes),
        "ltl": ltl(closes),
        "volume_ma5": ma(volumes, 5),
        "volume_ma10": ma(volumes, 10),
        "volume_ratio20": rolling_ratio(volumes, 20),
    }
    result.update({"dif": macd_values["dif"], "dea": macd_values["dea"], "macd": macd_values["macd"]})
    result.update({"kdj_k": kdj_values["k"], "kdj_d": kdj_values["d"], "kdj_j": kdj_values["j"]})
    return result


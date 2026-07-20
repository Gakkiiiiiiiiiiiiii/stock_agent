from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def _to_array(values: Sequence[float]) -> np.ndarray:
    return np.asarray(values, dtype=float)


def _to_series(values: Sequence[float]) -> pd.Series:
    return pd.Series(_to_array(values))


def _nan_to_none(values: np.ndarray) -> list[float | None]:
    # 前导不足窗口期的位置以 NaN 表示，对外仍按旧约定返回 None
    # tolist 在 C 层完成 float 转换，避免逐元素 np.isnan 的开销
    is_nan = np.isnan(values).tolist()
    return [None if m else v for v, m in zip(values.tolist(), is_nan)]


def _ma_arr(values: np.ndarray, window: int) -> np.ndarray:
    # 滑动窗口均值，前 window-1 个位置为 NaN（pairwise 求和，精度与逐 bar 累加相当）
    out = np.full(values.shape[0], np.nan)
    if values.shape[0] >= window:
        out[window - 1 :] = sliding_window_view(values, window).mean(axis=1)
    return out


def ma(values: Sequence[float], window: int) -> list[float | None]:
    if window <= 0:
        raise ValueError("window must be positive")
    return _nan_to_none(_ma_arr(_to_array(values), window))


def ema(values: Sequence[float], span: int) -> list[float]:
    if span <= 0:
        raise ValueError("span must be positive")
    if len(values) == 0:
        return []
    # adjust=False 时 y0=x0、y_t=alpha*x_t+(1-alpha)*y_{t-1}，与旧实现一致
    averaged = _to_series(values).ewm(span=span, adjust=False).mean()
    return averaged.tolist()


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, list[float]]:
    ema_fast = _to_array(ema(values, fast))
    ema_slow = _to_array(ema(values, slow))
    dif = ema_fast - ema_slow
    dea = _to_array(ema(dif, signal))
    hist = (dif - dea) * 2
    return {"dif": dif.tolist(), "dea": dea.tolist(), "macd": hist.tolist()}


def _ewm_seeded(values: np.ndarray, alpha: float, seed: float) -> np.ndarray:
    # 在序列前补一个 seed 值，利用 ewm(adjust=False) 的 y0=x0 特性实现带初值的递推
    seeded = pd.Series(np.concatenate(([seed], values)))
    return seeded.ewm(alpha=alpha, adjust=False).mean().to_numpy()[1:]


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
    length = len(closes)
    high_arr = _to_array(highs)
    low_arr = _to_array(lows)
    close_arr = _to_array(closes)

    high_n = np.full(length, np.nan)
    low_n = np.full(length, np.nan)
    if length >= n:
        high_n[n - 1 :] = sliding_window_view(high_arr, n).max(axis=1)
        low_n[n - 1 :] = sliding_window_view(low_arr, n).min(axis=1)

    with np.errstate(invalid="ignore", divide="ignore"):
        rsv = (close_arr - low_n) / (high_n - low_n) * 100
    # 窗口内最高价==最低价（一字板）时 rsv 按旧约定取 50
    flat = (high_n == low_n) & ~np.isnan(high_n)
    rsv = np.where(flat, 50.0, rsv)

    if length >= n:
        k_tail = _ewm_seeded(rsv[n - 1 :], 1 / m1, 50.0)
        d_tail = _ewm_seeded(k_tail, 1 / m2, 50.0)
        k_values = np.concatenate((np.full(n - 1, np.nan), k_tail))
        d_values = np.concatenate((np.full(n - 1, np.nan), d_tail))
    else:
        k_values = np.full(length, np.nan)
        d_values = np.full(length, np.nan)

    j_values = 3 * k_values - 2 * d_values
    return {
        "rsv": _nan_to_none(rsv),
        "k": _nan_to_none(k_values),
        "d": _nan_to_none(d_values),
        "j": _nan_to_none(j_values),
    }


def stl(closes: Sequence[float]) -> list[float]:
    return ema(ema(closes, 10), 10)


def ltl(closes: Sequence[float]) -> list[float | None]:
    close_arr = _to_array(closes)
    stacked = np.vstack([_ma_arr(close_arr, window) for window in (14, 28, 57, 114)])
    # 任一分量缺失（NaN）时整体为 None，否则取四线均值
    return _nan_to_none(stacked.mean(axis=0))


def rolling_ratio(values: Sequence[float], window: int) -> list[float | None]:
    if window <= 0:
        raise ValueError("window must be positive")
    avg_arr = _ma_arr(_to_array(values), window)
    value_arr = _to_array(values)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = value_arr / avg_arr
    # 均线缺失或为 0 时比值无意义，按旧约定返回 None
    invalid = np.isnan(avg_arr) | (avg_arr == 0)
    ratio = np.where(invalid, np.nan, ratio)
    return _nan_to_none(ratio)


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

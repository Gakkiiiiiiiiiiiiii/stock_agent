"""向量化指标与旧逐 bar 循环实现的逐值一致性比对。

参考实现（_ref_*）为改写前 engines/technical/indicators.py 的原样拷贝，
新实现必须与其在容差 1e-10 内逐值一致（None/前导缺失位置完全一致）。
"""

from __future__ import annotations

import numpy as np
import pytest

from engines.technical import indicators as new
from engines.technical.indicators import calc_all

TOL = 1e-10


# ---------------- 旧实现（参考） ----------------

def _ref_ma(values, window):
    if window <= 0:
        raise ValueError("window must be positive")
    result = []
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= window:
            running -= values[i - window]
        result.append(running / window if i >= window - 1 else None)
    return result


def _ref_ema(values, span):
    if span <= 0:
        raise ValueError("span must be positive")
    if not values:
        return []
    alpha = 2 / (span + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _ref_macd(values, fast=12, slow=26, signal=9):
    ema_fast = _ref_ema(values, fast)
    ema_slow = _ref_ema(values, slow)
    dif = [a - b for a, b in zip(ema_fast, ema_slow)]
    dea = _ref_ema(dif, signal)
    hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return {"dif": dif, "dea": dea, "macd": hist}


def _ref_kdj(highs, lows, closes, n=9, m1=3, m2=3):
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows and closes must have the same length")
    k_raw = []
    k_values = []
    d_values = []
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


def _ref_stl(closes):
    return _ref_ema(_ref_ema(closes, 10), 10)


def _ref_ltl(closes):
    ma14 = _ref_ma(closes, 14)
    ma28 = _ref_ma(closes, 28)
    ma57 = _ref_ma(closes, 57)
    ma114 = _ref_ma(closes, 114)
    result = []
    for values in zip(ma14, ma28, ma57, ma114):
        result.append(None if any(v is None for v in values) else sum(v for v in values if v is not None) / 4)
    return result


def _ref_rolling_ratio(values, window):
    avg = _ref_ma(values, window)
    return [None if a in (None, 0) else value / a for value, a in zip(values, avg)]


# ---------------- 比对工具 ----------------

def _assert_series_equal(actual, expected):
    assert len(actual) == len(expected), f"长度不一致: {len(actual)} vs {len(expected)}"
    for i, (a, e) in enumerate(zip(actual, expected)):
        if e is None:
            assert a is None, f"位置 {i}: 期望 None, 实际 {a}"
        else:
            assert a is not None, f"位置 {i}: 期望 {e}, 实际 None"
            assert abs(a - e) <= TOL, f"位置 {i}: 期望 {e}, 实际 {a}"


# ---------------- 用例 ----------------

LENGTHS = [0, 1, 5, 60, 300]


@pytest.fixture(params=LENGTHS)
def series(request):
    rng = np.random.default_rng(42 + request.param)
    n = request.param
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    highs = [c + abs(float(rng.normal(0, 0.5))) for c in closes]
    lows = [c - abs(float(rng.normal(0, 0.5))) for c in closes]
    volumes = (rng.uniform(1e5, 1e7, n)).tolist()
    return highs, lows, closes, volumes


@pytest.mark.parametrize("window", [1, 3, 5, 10, 20, 60, 120, 240])
def test_ma(series, window):
    _, _, closes, _ = series
    _assert_series_equal(new.ma(closes, window), _ref_ma(closes, window))


@pytest.mark.parametrize("span", [1, 2, 10, 12, 26])
def test_ema(series, span):
    _, _, closes, _ = series
    _assert_series_equal(new.ema(closes, span), _ref_ema(closes, span))


def test_macd(series):
    _, _, closes, _ = series
    actual = new.macd(closes)
    expected = _ref_macd(closes)
    assert set(actual) == set(expected)
    for key in ("dif", "dea", "macd"):
        _assert_series_equal(actual[key], expected[key])


def test_kdj(series):
    highs, lows, closes, _ = series
    actual = new.kdj(highs, lows, closes)
    expected = _ref_kdj(highs, lows, closes)
    assert set(actual) == set(expected)
    for key in ("rsv", "k", "d", "j"):
        _assert_series_equal(actual[key], expected[key])


def test_kdj_flat_window():
    # 窗口内最高价==最低价时 rsv 取 50
    highs = [10.0] * 12
    lows = [10.0] * 12
    closes = [10.0] * 12
    actual = new.kdj(highs, lows, closes)
    expected = _ref_kdj(highs, lows, closes)
    for key in ("rsv", "k", "d", "j"):
        _assert_series_equal(actual[key], expected[key])
    assert actual["rsv"][-1] == 50.0


def test_stl(series):
    _, _, closes, _ = series
    _assert_series_equal(new.stl(closes), _ref_stl(closes))


def test_ltl(series):
    _, _, closes, _ = series
    _assert_series_equal(new.ltl(closes), _ref_ltl(closes))


@pytest.mark.parametrize("window", [5, 20])
def test_rolling_ratio(series, window):
    _, _, _, volumes = series
    _assert_series_equal(new.rolling_ratio(volumes, window), _ref_rolling_ratio(volumes, window))


def test_rolling_ratio_zero_avg():
    # 均线为 0 时比值为 None
    volumes = [0.0] * 30
    actual = new.rolling_ratio(volumes, 20)
    expected = _ref_rolling_ratio(volumes, 20)
    _assert_series_equal(actual, expected)
    assert all(v is None for v in actual)


def test_calc_all(series):
    highs, lows, closes, volumes = series
    result = calc_all(highs, lows, closes, volumes)
    expected_keys = {
        "ma5", "ma10", "ma20", "ma60", "ma120", "ma240", "stl", "ltl",
        "volume_ma5", "volume_ma10", "volume_ratio20",
        "dif", "dea", "macd", "kdj_k", "kdj_d", "kdj_j",
    }
    assert set(result) == expected_keys
    for values in result.values():
        assert len(values) == len(closes)

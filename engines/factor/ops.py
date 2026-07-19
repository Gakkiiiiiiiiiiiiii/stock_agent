"""因子 DSL 算子实现。

所有算子基于 numpy，在形状为 (n_symbols, n_days) 的特征面板上运算：
- 时序算子（ts_*_N）沿 axis=1（时间轴）滚动计算，前 window-1 个值为 NaN；
- 横截面算子（cs_*）沿 axis=0（逐日截面）计算；
- 一元/二元算子逐元素计算。

无效数值约定：NaN 表示缺失；除零、log(<=0) 等非法运算返回 NaN。
"""
from __future__ import annotations

import re
import warnings

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from engines.factor.vocab import BINARY_OPS, CS_OPS, TS_OPS, TS_WINDOWS, UNARY_OPS

_EPS = 1e-12
_TS_TOKEN_RE = re.compile(r"^(ts_(?:mean|std|max|min|delta|delay|rank))_(\d+)$")


def _rolling_apply(x: np.ndarray, window: int, func) -> np.ndarray:
    """沿时间轴滚动应用聚合函数，前 window-1 个位置为 NaN。"""
    out = np.full(x.shape, np.nan, dtype=float)
    if window <= 0 or x.shape[1] < window:
        return out
    views = sliding_window_view(x, window, axis=1)  # (n_symbols, n_days-window+1, window)
    with warnings.catch_warnings():
        # 含全 NaN 窗口时 nanmean/nanstd 会发 RuntimeWarning，结果本就是 NaN，直接抑制
        warnings.simplefilter("ignore", RuntimeWarning)
        out[:, window - 1:] = func(views)
    return out


def _ts_mean(x: np.ndarray, window: int) -> np.ndarray:
    return _rolling_apply(x, window, lambda v: np.nanmean(v, axis=-1))


def _ts_std(x: np.ndarray, window: int) -> np.ndarray:
    return _rolling_apply(x, window, lambda v: np.nanstd(v, axis=-1))


def _ts_max(x: np.ndarray, window: int) -> np.ndarray:
    return _rolling_apply(x, window, lambda v: np.nanmax(v, axis=-1))


def _ts_min(x: np.ndarray, window: int) -> np.ndarray:
    return _rolling_apply(x, window, lambda v: np.nanmin(v, axis=-1))


def _ts_delay(x: np.ndarray, window: int) -> np.ndarray:
    out = np.full(x.shape, np.nan, dtype=float)
    out[:, window:] = x[:, :-window]
    return out


def _ts_delta(x: np.ndarray, window: int) -> np.ndarray:
    return x - _ts_delay(x, window)


def _ts_rank(x: np.ndarray, window: int) -> np.ndarray:
    """当前值在过去 window 天内的分位（0~1）。"""

    def _rank(v: np.ndarray) -> np.ndarray:
        current = v[:, :, -1:]  # (n, t, 1)
        valid = ~np.isnan(v)
        count = valid.sum(axis=-1)
        less_equal = ((v <= current) & valid).sum(axis=-1)
        with np.errstate(invalid="ignore", divide="ignore"):
            rank = np.where(count > 0, less_equal / np.maximum(count, 1), np.nan)
        rank = np.where(np.isnan(current[:, :, 0]), np.nan, rank)
        return rank

    return _rolling_apply(x, window, _rank)


_TS_FUNCS = {
    "ts_mean": _ts_mean,
    "ts_std": _ts_std,
    "ts_max": _ts_max,
    "ts_min": _ts_min,
    "ts_delta": _ts_delta,
    "ts_delay": _ts_delay,
    "ts_rank": _ts_rank,
}


def parse_ts_token(token: str) -> tuple[str, int] | None:
    """解析 `ts_mean_10` 形式的时序 token，返回 (算子名, 窗口)。"""
    m = _TS_TOKEN_RE.match(token)
    if not m:
        return None
    name, window = m.group(1), int(m.group(2))
    if window not in TS_WINDOWS:
        return None
    return name, window


def _cs_rank(x: np.ndarray) -> np.ndarray:
    """逐日截面分位（0~1），NaN 保持 NaN。"""
    out = np.full(x.shape, np.nan, dtype=float)
    for d in range(x.shape[1]):
        col = x[:, d]
        valid = ~np.isnan(col)
        n = valid.sum()
        if n == 0:
            continue
        order = np.argsort(col[valid], kind="mergesort")
        ranks = np.empty(n, dtype=float)
        ranks[order] = (np.arange(n) + 1) / n
        out[valid, d] = ranks
    return out


def _cs_zscore(x: np.ndarray) -> np.ndarray:
    mean = np.nanmean(x, axis=0, keepdims=True)
    std = np.nanstd(x, axis=0, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (x - mean) / np.where(std < _EPS, np.nan, std)
    return z


def _cs_demean(x: np.ndarray) -> np.ndarray:
    mean = np.nanmean(x, axis=0, keepdims=True)
    return x - mean


def _safe_div(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(np.abs(b) < _EPS, np.nan, a / np.where(np.abs(b) < _EPS, 1.0, b))


def _safe_log(x: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(x > 0, np.log(np.where(x > 0, x, 1.0)), np.nan)


def get_op(token: str) -> tuple[object, int] | None:
    """按 token 返回 (函数, 元数)，未知 token 返回 None。"""
    parsed = parse_ts_token(token)
    if parsed:
        name, window = parsed
        func = _TS_FUNCS[name]
        return (lambda x, _f=func, _w=window: _f(x, _w)), 1

    if token in CS_OPS:
        return {"cs_rank": _cs_rank, "cs_zscore": _cs_zscore, "cs_demean": _cs_demean}[token], 1

    if token in UNARY_OPS:
        unary = {
            "neg": lambda x: -x,
            "abs": np.abs,
            "log": _safe_log,
            "sqrt": lambda x: np.where(x >= 0, np.sqrt(np.where(x >= 0, x, 0.0)), np.nan),
            "sign": np.sign,
        }
        return unary[token], 1

    if token in BINARY_OPS:
        binary = {
            "add": lambda a, b: a + b,
            "sub": lambda a, b: a - b,
            "mul": lambda a, b: a * b,
            "div": _safe_div,
            "gt": lambda a, b: (a > b).astype(float),
            "lt": lambda a, b: (a < b).astype(float),
            "max": np.maximum,
            "min": np.minimum,
        }
        return binary[token], 2

    return None


__all__ = ["get_op", "parse_ts_token"]

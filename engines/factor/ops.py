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

from engines.factor.vocab import (
    BINARY_OPS,
    CS_OPS,
    TERNARY_OPS,
    TS_BINARY_OPS,
    TS_OPS,
    TS_WINDOWS,
    UNARY_OPS,
)

_EPS = 1e-12
_TS_TOKEN_RE = re.compile(
    r"^(ts_mean|ts_std|ts_max|ts_min|ts_delta|ts_delay|ts_rank|ts_sum"
    r"|ts_corr|ts_cov|ts_argmax|ts_argmin|decay_linear|count)_(\d+)$"
)


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


def _ts_sum(x: np.ndarray, window: int) -> np.ndarray:
    """窗内求和：NaN 视为缺失跳过，全 NaN 窗输出 NaN（nansum 全 NaN 默认得 0，需额外掩蔽）。"""

    def _sum(v: np.ndarray) -> np.ndarray:
        s = np.nansum(v, axis=-1)
        return np.where(np.isnan(v).all(axis=-1), np.nan, s)

    return _rolling_apply(x, window, _sum)


def _decay_linear(x: np.ndarray, window: int) -> np.ndarray:
    """线性衰减加权平均：权重 w_i=i/sum(i)（i 由远及近 1..window），最新值权重最大。"""
    weights = np.arange(1, window + 1, dtype=float)
    weights /= weights.sum()

    def _wma(v: np.ndarray) -> np.ndarray:
        valid = ~np.isnan(v)
        w_sum = (valid * weights).sum(axis=-1)
        weighted = np.nansum(v * weights, axis=-1)
        with np.errstate(invalid="ignore", divide="ignore"):
            # 按有效值的权重和归一化；全 NaN 窗（w_sum=0）输出 NaN
            return np.where(w_sum > 0, weighted / np.where(w_sum > 0, w_sum, 1.0), np.nan)

    return _rolling_apply(x, window, _wma)


def _ts_argextreme(x: np.ndarray, window: int, pick_max: bool) -> np.ndarray:
    """窗内极值距今的天数（0 表示极值就在当期），全 NaN 窗输出 NaN。"""
    fill = -np.inf if pick_max else np.inf

    def _arg(v: np.ndarray) -> np.ndarray:
        all_nan = np.isnan(v).all(axis=-1)
        masked = np.where(np.isnan(v), fill, v)
        idx = masked.argmax(axis=-1) if pick_max else masked.argmin(axis=-1)
        dist = (v.shape[-1] - 1 - idx).astype(float)
        return np.where(all_nan, np.nan, dist)

    return _rolling_apply(x, window, _arg)


def _ts_argmax(x: np.ndarray, window: int) -> np.ndarray:
    return _ts_argextreme(x, window, pick_max=True)


def _ts_argmin(x: np.ndarray, window: int) -> np.ndarray:
    return _ts_argextreme(x, window, pick_max=False)


def _count(x: np.ndarray, window: int) -> np.ndarray:
    """窗内 x>0 的天数；NaN 不计入，全 NaN 窗输出 NaN。"""

    def _cnt(v: np.ndarray) -> np.ndarray:
        all_nan = np.isnan(v).all(axis=-1)
        c = (v > 0).sum(axis=-1).astype(float)
        return np.where(all_nan, np.nan, c)

    return _rolling_apply(x, window, _cnt)


def _rolling_apply_pair(a: np.ndarray, b: np.ndarray, window: int, func) -> np.ndarray:
    """二元时序算子的滚动框架：对两个面板同步取滑动窗，前 window-1 个位置为 NaN。"""
    out = np.full(a.shape, np.nan, dtype=float)
    if window <= 0 or a.shape[1] < window:
        return out
    va = sliding_window_view(a, window, axis=1)  # (n_symbols, n_days-window+1, window)
    vb = sliding_window_view(b, window, axis=1)
    out[:, window - 1:] = func(va, vb)
    return out


def _pair_stats(va: np.ndarray, vb: np.ndarray):
    """窗内有效配对（剔除任一序列为 NaN 的位置）的和/平方和/交叉和。"""
    valid = ~(np.isnan(va) | np.isnan(vb))
    n = valid.sum(axis=-1)
    xa = np.where(valid, va, 0.0)
    xb = np.where(valid, vb, 0.0)
    sx = xa.sum(axis=-1)
    sy = xb.sum(axis=-1)
    sxx = (xa * xa).sum(axis=-1)
    syy = (xb * xb).sum(axis=-1)
    sxy = (xa * xb).sum(axis=-1)
    return n, sx, sy, sxx, syy, sxy


def _ts_corr(a: np.ndarray, b: np.ndarray, window: int) -> np.ndarray:
    """窗内皮尔逊相关系数：有效配对 <2 或任一序列零方差时输出 NaN。"""

    def _corr(va: np.ndarray, vb: np.ndarray) -> np.ndarray:
        n, sx, sy, sxx, syy, sxy = _pair_stats(va, vb)
        with np.errstate(invalid="ignore", divide="ignore"):
            nf = np.maximum(n, 1)
            cov = sxy / nf - (sx / nf) * (sy / nf)
            vx = sxx / nf - (sx / nf) ** 2
            vy = syy / nf - (sy / nf) ** 2
            denom = np.sqrt(np.maximum(vx, 0.0) * np.maximum(vy, 0.0))
            corr = cov / denom
        return np.where((n >= 2) & (denom > _EPS), corr, np.nan)

    return _rolling_apply_pair(a, b, window, _corr)


def _ts_cov(a: np.ndarray, b: np.ndarray, window: int) -> np.ndarray:
    """窗内总体协方差（ddof=0，与 ts_std 的 nanstd 口径一致）：有效配对 <2 时输出 NaN。"""

    def _cov(va: np.ndarray, vb: np.ndarray) -> np.ndarray:
        n, sx, sy, _, _, sxy = _pair_stats(va, vb)
        with np.errstate(invalid="ignore", divide="ignore"):
            nf = np.maximum(n, 1)
            cov = sxy / nf - (sx / nf) * (sy / nf)
        return np.where(n >= 2, cov, np.nan)

    return _rolling_apply_pair(a, b, window, _cov)


_TS_FUNCS = {
    "ts_mean": _ts_mean,
    "ts_std": _ts_std,
    "ts_max": _ts_max,
    "ts_min": _ts_min,
    "ts_delta": _ts_delta,
    "ts_delay": _ts_delay,
    "ts_rank": _ts_rank,
    "ts_sum": _ts_sum,
    "decay_linear": _decay_linear,
    "ts_argmax": _ts_argmax,
    "ts_argmin": _ts_argmin,
    "count": _count,
}

_TS_BINARY_FUNCS = {
    "ts_corr": _ts_corr,
    "ts_cov": _ts_cov,
}


def parse_ts_token(token: str) -> tuple[str, int] | None:
    """解析 `ts_mean_10` / `ts_corr_10` / `decay_linear_10` 形式的时序 token，返回 (算子名, 窗口)。"""
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
        if name in TS_BINARY_OPS:
            func = _TS_BINARY_FUNCS[name]
            return (lambda a, b, _f=func, _w=window: _f(a, b, _w)), 2
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
            "signedpower": lambda x: np.sign(x) * x * x,
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

    if token in TERNARY_OPS:
        ternary = {
            # where(cond, a, b)：cond>0 取 a 否则取 b；cond 为 NaN 时按 False 处理取 b
            "where": lambda cond, a, b: np.where(cond > 0, a, b),
        }
        return ternary[token], 3

    return None


__all__ = ["get_op", "parse_ts_token"]

"""因子适应度评估：RankIC / IC / ICIR / TopK 组合回测。

全部为样本内横截面评估，仅供因子筛选参考，不构成收益承诺。
"""
from __future__ import annotations

import numpy as np

MIN_VALID_PER_DAY = 10      # 单日截面有效标的最少数量
MIN_COVERAGE = 0.6          # 有效 IC 天数占比下限，不足直接淘汰
TOP_K_RATIO = 0.01          # TopK 组合持仓比例（池子的 1%）
TOP_K_MIN = 5               # TopK 下限
TURNOVER_COST = 0.001       # 双边换手成本率
TRADING_DAYS_PER_YEAR = 250

# 入库阈值
RANK_IC_THRESHOLD = 0.02
ICIR_THRESHOLD = 0.2


def _rank(values: np.ndarray) -> np.ndarray:
    """一维数组升序分位（1..n）/n，NaN 保持 NaN。"""
    out = np.full(values.shape, np.nan, dtype=float)
    valid = ~np.isnan(values)
    n = valid.sum()
    if n == 0:
        return out
    order = np.argsort(values[valid], kind="mergesort")
    ranks = np.empty(n, dtype=float)
    ranks[order] = (np.arange(n) + 1) / n
    out[valid] = ranks
    return out


def evaluate_factor(
    factor_panel: np.ndarray,
    closes: np.ndarray,
    horizon: int = 5,
    top_k: int | None = None,
    eval_window: int | None = None,
) -> dict:
    """评估因子面板，返回指标字典。

    factor_panel / closes 形状均为 (n_symbols, n_days)。
    前瞻收益 fwd[t] = close[t+horizon]/close[t] - 1（不足 horizon 的尾部为 NaN）。
    eval_window 指定时只在最近 eval_window 个交易日上评估（因子值仍用全量历史计算，
    保证时序算子有足够回看窗口）。
    """
    n_symbols, n_days = factor_panel.shape
    start_d = max(0, n_days - eval_window) if eval_window else 0
    # TopK 比例化：全 A 大池下固定 5 只过于极端，默认取池子的 1%（下限 5 只）
    resolved_top_k = top_k or max(TOP_K_MIN, int(n_symbols * TOP_K_RATIO))
    fwd = np.full((n_symbols, n_days), np.nan, dtype=float)
    if n_days > horizon:
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd[:, :-horizon] = closes[:, horizon:] / closes[:, :-horizon] - 1.0

    ic_list: list[float] = []
    rank_ic_list: list[float] = []
    topk_daily: list[tuple[int, float, set[int]]] = []  # (day, 组合日收益, 持仓索引集合)

    for d in range(start_d, n_days):
        f = factor_panel[:, d]
        r = fwd[:, d]
        valid = ~np.isnan(f) & ~np.isnan(r)
        if valid.sum() < MIN_VALID_PER_DAY:
            continue
        fv, rv = f[valid], r[valid]
        if np.std(fv) < 1e-12 or np.std(rv) < 1e-12:
            continue
        ic_list.append(float(np.corrcoef(fv, rv)[0, 1]))
        rank_ic_list.append(float(np.corrcoef(_rank(fv), _rank(rv))[0, 1]))

        # TopK 等权多头组合：按因子值取前 top_k，持有 horizon 日后的平均前瞻收益折算为日收益
        k = min(resolved_top_k, int(valid.sum()))
        idx = np.where(valid)[0]
        top_idx = set(idx[np.argsort(fv)[-k:]].tolist())
        topk_daily.append((d, float(np.mean(rv) / horizon), top_idx))

    total_days = n_days - start_d
    coverage = len(ic_list) / total_days if total_days else 0.0
    if coverage < MIN_COVERAGE or not rank_ic_list:
        return {
            "rank_ic": 0.0, "ic_mean": 0.0, "icir": 0.0,
            "topk_annual_return": 0.0, "topk_max_drawdown": 0.0,
            "coverage": round(coverage, 4), "fitness": float("-inf"),
            "passed": False,
        }

    ic_mean = float(np.mean(ic_list))
    rank_ic = float(np.mean(rank_ic_list))
    ic_std = float(np.std(rank_ic_list))
    if ic_std > 1e-12:
        icir = rank_ic / ic_std
    else:
        # 日 IC 无波动（如完美预测）：用 0.01 作为波动下限避免 ICIR 退化为 0
        icir = rank_ic / 0.01 if rank_ic != 0 else 0.0

    # TopK 组合净值：逐日换仓，扣除双边换手成本
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    prev_holdings: set[int] | None = None
    daily_returns: list[float] = []
    for _, day_ret, holdings in topk_daily:
        turnover = 1.0 if prev_holdings is None else len(holdings - prev_holdings) / max(len(holdings), 1)
        net_ret = day_ret - TURNOVER_COST * turnover
        daily_returns.append(net_ret)
        equity *= 1.0 + net_ret
        peak = max(peak, equity)
        max_dd = max(max_dd, 1.0 - equity / peak)
        prev_holdings = holdings
    annual_return = float(np.mean(daily_returns) * TRADING_DAYS_PER_YEAR) if daily_returns else 0.0

    # 综合适应度：RankIC 为主，ICIR 衡量稳定性，TopK 年化衡量多头端可交易性
    fitness = 5.0 * rank_ic + 0.5 * icir + annual_return
    passed = rank_ic >= RANK_IC_THRESHOLD and icir >= ICIR_THRESHOLD
    return {
        "rank_ic": round(rank_ic, 4),
        "ic_mean": round(ic_mean, 4),
        "icir": round(icir, 4),
        "topk_annual_return": round(annual_return, 4),
        "topk_max_drawdown": round(max_dd, 4),
        "coverage": round(coverage, 4),
        "fitness": round(fitness, 4),
        "top_k": resolved_top_k,
        "passed": bool(passed),
    }


__all__ = ["evaluate_factor", "RANK_IC_THRESHOLD", "ICIR_THRESHOLD", "MIN_COVERAGE"]

"""因子适应度评估：RankIC / IC / ICIR / TopK 组合回测。

全部为样本内横截面评估，仅供因子筛选参考，不构成收益承诺。
"""
from __future__ import annotations

import numpy as np

from financial_agent.research_config import EvaluationConfig, get_research_config

MIN_VALID_PER_DAY = 10      # 单日截面有效标的最少数量
MIN_COVERAGE = 0.6          # 有效 IC 天数占比下限，不足直接淘汰
TOP_K_RATIO = 0.01          # TopK 组合持仓比例（池子的 1%）
TOP_K_MIN = 5               # TopK 下限
TURNOVER_COST = 0.001       # 双边换手成本率
TRADING_DAYS_PER_YEAR = 250

# 入库阈值
RANK_IC_THRESHOLD = 0.02
ICIR_THRESHOLD = 0.3
TOPK_EXCESS_THRESHOLD = 0.0


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
    thresholds: EvaluationConfig | None = None,
) -> dict:
    """评估因子面板，返回指标字典。

    factor_panel / closes 形状均为 (n_symbols, n_days)。
    前瞻收益 fwd[t] = close[t+horizon]/close[t] - 1（不足 horizon 的尾部为 NaN）。
    eval_window 指定时只在最近 eval_window 个交易日上评估（因子值仍用全量历史计算，
    保证时序算子有足够回看窗口）。
    """
    _, n_days = factor_panel.shape
    valid_end = max(0, n_days - horizon)
    start_d = max(0, valid_end - eval_window) if eval_window else 0
    return evaluate_factor_range(
        factor_panel,
        closes,
        eval_start=start_d,
        eval_end=valid_end,
        horizon=horizon,
        top_k=top_k,
        thresholds=thresholds,
    )


def evaluate_factor_range(
    factor_panel: np.ndarray,
    closes: np.ndarray,
    eval_start: int,
    eval_end: int,
    horizon: int = 5,
    top_k: int | None = None,
    thresholds: EvaluationConfig | None = None,
) -> dict:
    """Evaluate factor dates in [eval_start, eval_end) only.

    The close matrix must include prices through eval_end + horizon so forward
    returns are observable, but those future observation days are not counted in
    the coverage denominator.
    """

    n_symbols, n_days = factor_panel.shape
    thresholds = thresholds or get_research_config().evaluation
    eval_start = max(0, int(eval_start))
    eval_end = min(int(eval_end), n_days - horizon)
    if eval_end <= eval_start:
        return {
            "rank_ic": 0.0, "ic_mean": 0.0, "icir": 0.0,
            "topk_annual_return": 0.0, "topk_max_drawdown": 0.0,
            "coverage": 0.0, "fitness": float("-inf"),
            "evaluated_days": 0,
            "valid_ic_days": 0,
            "passed": False,
            "warning": "eval range too short",
        }
    # TopK 比例化：全 A 大池下固定 5 只过于极端，默认取池子的 1%（下限 5 只）
    resolved_top_k = top_k or max(TOP_K_MIN, int(n_symbols * TOP_K_RATIO))
    fwd = np.full((n_symbols, n_days), np.nan, dtype=float)
    if n_days > horizon:
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd[:, :-horizon] = closes[:, horizon:] / closes[:, :-horizon] - 1.0

    ic_list: list[float] = []
    rank_ic_list: list[float] = []
    topk_daily: list[tuple[int, float, float, set[int]]] = []  # (day, TopK日收益, 基准日收益, 持仓索引集合)
    last_topk_day = eval_start - horizon

    for d in range(eval_start, eval_end):
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

        if d - last_topk_day < horizon:
            continue
        last_topk_day = d

        # TopK 等权多头组合：只统计 TopK 标的收益，不能退化为全截面均值。
        k = min(resolved_top_k, int(valid.sum()))
        idx = np.where(valid)[0]
        local_top = np.argsort(fv, kind="mergesort")[-k:]
        top_global_idx = idx[local_top]
        top_idx = set(top_global_idx.tolist())
        top_return = float(np.nanmean(fwd[top_global_idx, d]))
        benchmark_return = float(np.nanmean(rv))
        topk_daily.append((d, top_return, benchmark_return, top_idx))

    total_days = eval_end - eval_start
    coverage = len(ic_list) / total_days if total_days else 0.0
    if coverage < thresholds.min_coverage or not rank_ic_list:
        return {
            "rank_ic": 0.0, "ic_mean": 0.0, "icir": 0.0,
            "topk_annual_return": 0.0, "topk_max_drawdown": 0.0,
            "coverage": round(coverage, 4), "fitness": float("-inf"),
            "evaluated_days": total_days,
            "valid_ic_days": len(ic_list),
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

    # TopK 辅助组合评价：使用每个信号日的非重叠前瞻收益序列做准入参考。
    # 这里不把重叠 N 日收益简单除以 N 拼成逐日净值，避免伪净值。
    topk_equity = 1.0
    benchmark_equity = 1.0
    peak = 1.0
    max_dd = 0.0
    prev_holdings: set[int] | None = None
    net_period_returns: list[float] = []
    benchmark_period_returns: list[float] = []
    for _, period_ret, benchmark_ret, holdings in topk_daily:
        turnover = 1.0 if prev_holdings is None else len(holdings - prev_holdings) / max(len(holdings), 1)
        net_ret = period_ret - TURNOVER_COST * turnover
        net_period_returns.append(net_ret)
        benchmark_period_returns.append(benchmark_ret)
        topk_equity *= 1.0 + net_ret
        benchmark_equity *= 1.0 + benchmark_ret
        peak = max(peak, topk_equity)
        max_dd = max(max_dd, 1.0 - topk_equity / peak)
        prev_holdings = holdings
    periods_per_year = TRADING_DAYS_PER_YEAR / max(horizon, 1)
    annual_return = float(np.mean(net_period_returns) * periods_per_year) if net_period_returns else 0.0
    benchmark_annual_return = (
        float(np.mean(benchmark_period_returns) * periods_per_year) if benchmark_period_returns else 0.0
    )
    topk_excess_annual_return = annual_return - benchmark_annual_return

    # 综合适应度：RankIC 为主，ICIR 衡量稳定性，TopK 年化衡量多头端可交易性
    fitness = 5.0 * rank_ic + 0.5 * icir + topk_excess_annual_return
    passed = (
        rank_ic >= thresholds.min_rank_ic
        and icir >= thresholds.min_icir
        and topk_excess_annual_return > thresholds.min_topk_excess_annual_return
        and annual_return > benchmark_annual_return
    )
    return {
        "rank_ic": round(rank_ic, 4),
        "ic_mean": round(ic_mean, 4),
        "icir": round(icir, 4),
        "topk_annual_return": round(annual_return, 4),
        "benchmark_annual_return": round(benchmark_annual_return, 4),
        "topk_excess_annual_return": round(topk_excess_annual_return, 4),
        "topk_max_drawdown": round(max_dd, 4),
        "coverage": round(coverage, 4),
        "evaluated_days": total_days,
        "valid_ic_days": len(ic_list),
        "fitness": round(fitness, 4),
        "top_k": resolved_top_k,
        "passed": bool(passed),
    }


__all__ = [
    "evaluate_factor",
    "evaluate_factor_range",
    "RANK_IC_THRESHOLD",
    "ICIR_THRESHOLD",
    "TOPK_EXCESS_THRESHOLD",
    "MIN_COVERAGE",
]

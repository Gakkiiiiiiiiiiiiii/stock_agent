"""Walk-forward 滚动重挖预检。

在每个调仓日 T：只用截止 T 的面板切片（最近 250 个交易日窗口）跑 FactorMiner
（挖掘库写入临时文件并从上一点快照继承，模拟换血且不污染正式库）→ 快照该时点
active 因子 → 用 T 日截面合成 alpha_score 选 TopK → 从 T+1 起用
engines/backtest/portfolio_backtest 记账并持有 horizon 日 → 滚动到下一点。

挖掘与合成分数严格只用 ≤T 的列，回测记账严格只用 >T 的列，避免显性前视；
但 LLM 训练数据本身包含历史市场知识，存在范式自带的隐蔽前视，见 disclaimer。
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

import numpy as np

from engines.backtest.metrics import calc_portfolio_metrics
from engines.backtest.portfolio_backtest import run_topk_backtest
from engines.factor.alpha import compose_alpha_scores
from engines.factor.library import active_factors, load_library
from engines.factor.miner import FactorMiner

logger = logging.getLogger(__name__)

DEFAULT_START_DAY = 240  # 默认首个调仓点（留出挖掘窗口）
DEFAULT_STEP_DAYS = 20   # 默认调仓点间距
MINING_WINDOW = 250      # 挖掘用面板窗口长度（[T-249, T]）
TRADING_DAYS_PER_YEAR = 252

DISCLAIMER = (
    "本结果为样本内 walk-forward 滚动重挖预检：LLM 训练数据包含历史市场知识，"
    "挖掘范式自带隐蔽前视（lookahead）风险，样本内超额可能被高估。"
    "本预检仅作辅助判据，因子有效性最终以每日前向模拟盘"
    "（workers/factor_paper_worker）为准。不构成投资建议。"
)


def default_rebalance_points(n_days: int, start: int = DEFAULT_START_DAY, step: int = DEFAULT_STEP_DAYS) -> list[int]:
    """默认从第 240 天起每 20 天一个调仓点，末尾需留出至少 1 个记账日。"""
    return [t for t in range(start, n_days - 1, step)]


def _empty_result(warning: str) -> dict:
    return {
        "equity_curve": [], "benchmark_curve": [], "dates": [],
        "metrics": {}, "window_hit_rate": None, "per_window": [],
        "warning": warning, "disclaimer": DISCLAIMER,
    }


def run_walkforward(
    panel: dict[str, np.ndarray],
    dates: list[str],
    symbols: list[str],
    rebalance_points: list[int] | None = None,
    horizon: int = 5,
    rounds: int = 3,
    candidates_per_round: int = 8,
    top_k: int | None = None,
    library_path: str | None = None,
    model_client=None,
) -> dict:
    """执行 walk-forward 滚动重挖预检，返回净值/指标/分窗口明细。"""
    closes = panel.get("close")
    if closes is None or closes.size == 0 or not symbols:
        return _empty_result("特征面板为空，无法执行 walk-forward 预检")
    n_days = closes.shape[1]
    points = rebalance_points if rebalance_points is not None else default_rebalance_points(n_days)
    points = [t for t in points if 0 <= t < n_days - 1]
    if not points:
        return _empty_result("样本长度不足，无可用调仓点")

    resolved_top_k = top_k or max(5, int(len(symbols) * 0.01))
    base_lib = Path(library_path) if library_path else None

    equity_curve: list[float] = []
    benchmark_curve: list[float] = []
    wf_dates: list[str] = []
    all_trades: list[dict] = []
    all_turnover: list[float] = []
    per_window: list[dict] = []
    warnings: list[str] = []

    with tempfile.TemporaryDirectory(prefix="factor_wf_") as tmp_dir:
        tmp = Path(tmp_dir)
        prev_lib: Path | None = None
        for t in points:
            # 挖掘窗口：[T-249, T]，只用 ≤T 的列
            start = max(0, t - (MINING_WINDOW - 1))
            sub_panel = {name: values[:, start:t + 1] for name, values in panel.items()}

            # 挖掘库写入临时文件，从上一点快照继承（首点继承正式库），避免污染正式库
            cur_lib = tmp / f"lib_{t}.yaml"
            inherit = prev_lib if prev_lib is not None else base_lib
            if inherit is not None and Path(inherit).exists():
                shutil.copy(inherit, cur_lib)
            miner = FactorMiner(model_client=model_client, library_path=str(cur_lib))
            mining = miner.mine(
                sub_panel, symbols,
                rounds=rounds, candidates_per_round=candidates_per_round, horizon=horizon,
            )
            if mining.get("warning"):
                warnings.append(f"{dates[t]}: {mining['warning']}")

            if cur_lib.exists():
                library = load_library(cur_lib)
                prev_lib = cur_lib
            else:
                library = load_library(prev_lib) if prev_lib else {"factors": []}
            factors = active_factors(library)

            # T 日截面等权合成 alpha_score（因子面板只含 ≤T 的列，无显性前视）
            scores, factor_count = compose_alpha_scores(sub_panel, factors)
            picks: list[str] = []
            if scores is not None:
                valid_idx = np.where(~np.isnan(scores))[0]
                order = valid_idx[np.argsort(-scores[valid_idx])]
                picks = [symbols[i] for i in order[:resolved_top_k]]

            # 记账区间：T+1 起 horizon 日，只用 >T 的列；rebalance_interval=horizon
            # 保证区间内仅在首日按 T 日分数调仓一次，之后持有不动
            end = min(t + 1 + horizon, n_days)
            seg_dates = list(dates[t + 1:end])
            score_panel = np.full(closes[:, t + 1:end].shape, np.nan)
            if scores is not None:
                score_panel[:, 0] = scores
            seg = run_topk_backtest(
                score_panel,
                panel["open"][:, t + 1:end],
                panel["high"][:, t + 1:end],
                panel["low"][:, t + 1:end],
                closes[:, t + 1:end],
                panel["volume"][:, t + 1:end],
                symbols, seg_dates,
                rebalance_interval=horizon,
                top_k=resolved_top_k,
                initial_cash=equity_curve[-1] if equity_curve else 1_000_000.0,
            )

            seg_eq = seg["equity_curve"]
            seg_bench = seg["benchmark_curve"]
            window_return = seg_eq[-1] / seg_eq[0] - 1 if len(seg_eq) > 1 else 0.0
            bench_return = seg_bench[-1] / seg_bench[0] - 1 if len(seg_bench) > 1 else 0.0
            excess = window_return - bench_return
            per_window.append({
                "rebalance_date": dates[t],
                "window_start": seg_dates[0],
                "window_end": seg_dates[-1],
                "window_return": round(window_return, 4),
                "benchmark_return": round(bench_return, 4),
                "excess_return": round(excess, 4),
                "hit": bool(excess > 0),
                "factor_count": factor_count,
                "factor_ids": [f.get("id") for f in factors],
                "accepted_count": len(mining.get("accepted") or []),
                "picks": picks,
            })

            equity_curve.extend(seg_eq)
            benchmark_curve.extend(seg_bench)
            wf_dates.extend(seg_dates)
            all_trades.extend(seg["trades"])
            all_turnover.extend(seg["daily_turnover"])

    metrics = calc_portfolio_metrics(
        equity_curve, benchmark_curve, all_trades, all_turnover, wf_dates,
    )
    # 超额夏普：逐日（组合收益 - 基准收益）的均值/波动年化
    eq = np.asarray(equity_curve, dtype=float)
    bench = np.asarray(benchmark_curve, dtype=float)
    excess_sharpe = 0.0
    if eq.size > 1 and bench.size > 1:
        excess_daily = eq[1:] / eq[:-1] - bench[1:] / bench[:-1]
        std = float(excess_daily.std(ddof=1)) if excess_daily.size > 1 else 0.0
        if std > 0:
            excess_sharpe = float(excess_daily.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))
    metrics["excess_sharpe"] = round(excess_sharpe, 4)

    hits = [w["hit"] for w in per_window]
    return {
        "equity_curve": equity_curve,
        "benchmark_curve": benchmark_curve,
        "dates": wf_dates,
        "metrics": metrics,
        "window_hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
        "per_window": per_window,
        "warning": "; ".join(warnings) if warnings else None,
        "disclaimer": DISCLAIMER,
    }


__all__ = ["run_walkforward", "default_rebalance_points", "DISCLAIMER"]

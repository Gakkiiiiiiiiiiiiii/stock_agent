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
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np

from engines.backtest.metrics import calc_portfolio_metrics
from engines.backtest.portfolio_backtest import run_topk_backtest
from engines.factor.alpha import compose_alpha_scores
from engines.factor.data import build_panel_data_version
from engines.factor.library import load_library, research_validated_factors
from engines.factor.miner import FactorMiner
from engines.factor.research_window import resolve_research_window_requirement
from financial_agent.research_config import get_research_config

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


@dataclass(frozen=True)
class WalkForwardWindow:
    rebalance_index: int
    start_index: int
    end_index: int
    dates: list[str]
    panel: dict[str, np.ndarray]
    data_version: str
    snapshot_id: str


@dataclass(frozen=True)
class WalkForwardTarget:
    signal_date: str
    execution_date: str
    exit_date: str
    target_symbols: tuple[str, ...]
    scores: dict[str, float]
    factor_ids: tuple[str, ...]
    factor_count: int
    research_run_id: str | None
    window_data_version: str
    window_snapshot_id: str


def _build_walkforward_window(
    panel: dict[str, np.ndarray],
    dates: list[str],
    symbols: list[str],
    t: int,
    *,
    window_days: int = MINING_WINDOW,
) -> WalkForwardWindow:
    start = max(0, t - (window_days - 1))
    window_dates = list(dates[start:t + 1])
    sub_panel = {name: values[:, start:t + 1] for name, values in panel.items()}
    expected_days = len(window_dates)
    for name, values in sub_panel.items():
        if values.ndim != 2 or values.shape[1] != expected_days:
            raise ValueError(
                "WALKFORWARD_WINDOW_SHAPE_MISMATCH:"
                f"{name}:{values.shape}!={expected_days}"
            )
    data_version = build_panel_data_version(symbols, window_dates, sub_panel, "walkforward", "none")
    snapshot_id = f"walkforward:{window_dates[0]}:{window_dates[-1]}:{uuid4().hex[:12]}"
    return WalkForwardWindow(
        rebalance_index=t,
        start_index=start,
        end_index=t,
        dates=window_dates,
        panel=sub_panel,
        data_version=data_version,
        snapshot_id=snapshot_id,
    )


def default_rebalance_points(
    n_days: int,
    start: int | None = None,
    step: int = DEFAULT_STEP_DAYS,
    window_days: int | None = None,
) -> list[int]:
    """默认从第 240 天起每 20 天一个调仓点，末尾需留出至少 1 个记账日。"""
    resolved_window_days = window_days or resolve_research_window_requirement().resolved_window_days
    resolved_start = start if start is not None else resolved_window_days - 1
    return [t for t in range(resolved_start, n_days - 1, step)]


def _empty_result(warning: str, diagnostics: dict | None = None) -> dict:
    return {
        "equity_curve": [], "benchmark_curve": [], "dates": [],
        "metrics": {}, "window_hit_rate": None, "per_window": [],
        "target_schedule": [], "warning": warning, "disclaimer": DISCLAIMER,
        "mode": "continuous_walkforward", "status": "INVALID",
        "diagnostics": diagnostics or {},
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
    data_version: str | None = None,
    data_snapshot_id: str | None = None,
    security_meta=None,
    upper_limit_prices=None,
    lower_limit_prices=None,
    mining_window_days: int | None = None,
) -> dict:
    """执行 walk-forward 滚动重挖预检，返回净值/指标/分窗口明细。"""
    closes = panel.get("close")
    if closes is None or closes.size == 0 or not symbols:
        return _empty_result("特征面板为空，无法执行 walk-forward 预检")
    research_requirement = resolve_research_window_requirement()
    window_days = int(mining_window_days or research_requirement.resolved_window_days)
    if closes.shape[1] < window_days + 1:
        return _empty_result(
            "WALKFORWARD_SAMPLE_INSUFFICIENT",
            diagnostics={
                "available_days": int(closes.shape[1]),
                "required_window_days": window_days,
                "minimum_research_days": research_requirement.minimum_required_days,
            },
        )
    parent_data_version = data_version or build_panel_data_version(symbols, dates, panel, "walkforward", "none")
    parent_data_snapshot_id = data_snapshot_id or f"walkforward-{uuid4().hex[:12]}"
    n_days = closes.shape[1]
    points = (
        rebalance_points
        if rebalance_points is not None
        else default_rebalance_points(n_days, window_days=window_days)
    )
    points = [t for t in points if window_days - 1 <= t < n_days - 1]
    if not points:
        return _empty_result("样本长度不足，无可用调仓点")

    resolved_top_k = top_k or max(5, int(len(symbols) * 0.01))
    base_lib = Path(library_path) if library_path else None

    targets_by_execution_date: dict[str, WalkForwardTarget] = {}
    per_window: list[dict] = []
    warnings: list[str] = []

    with tempfile.TemporaryDirectory(prefix="factor_wf_") as tmp_dir:
        tmp = Path(tmp_dir)
        prev_lib: Path | None = None
        for t in points:
            # 挖掘窗口：[T-window_days+1, T]，只用 ≤T 的列
            window = _build_walkforward_window(panel, dates, symbols, t, window_days=window_days)

            # 挖掘库写入临时文件，从上一点快照继承（首点继承正式库），避免污染正式库
            cur_lib = tmp / f"lib_{t}.yaml"
            inherit = prev_lib if prev_lib is not None else base_lib
            if inherit is not None and Path(inherit).exists():
                shutil.copy(inherit, cur_lib)
            miner = FactorMiner(model_client=model_client, library_path=str(cur_lib))
            mining = miner.mine(
                window.panel, symbols,
                rounds=rounds, candidates_per_round=candidates_per_round, horizon=horizon,
                dates=window.dates,
                data_version=window.data_version,
                data_snapshot_id=window.snapshot_id,
                data_context={
                    "mode": "walkforward",
                    "parent_data_version": parent_data_version,
                    "parent_data_snapshot_id": parent_data_snapshot_id,
                    "window_start": window.dates[0],
                    "window_end": window.dates[-1],
                    "rebalance_date": dates[t],
                    "rebalance_index": t,
                },
            )
            if mining.get("warning"):
                warnings.append(f"{dates[t]}: {mining['warning']}")

            if cur_lib.exists():
                library = load_library(cur_lib)
                prev_lib = cur_lib
            else:
                library = load_library(prev_lib) if prev_lib else {"factors": []}
            factors = research_validated_factors(library)

            # T 日截面等权合成 alpha_score（因子面板只含 ≤T 的列，无显性前视）
            scores, factor_count = compose_alpha_scores(window.panel, factors)
            picks: list[str] = []
            if scores is not None:
                valid_idx = np.where(~np.isnan(scores))[0]
                order = valid_idx[np.argsort(-scores[valid_idx])]
                picks = [symbols[i] for i in order[:resolved_top_k]]

            end = min(t + 1 + horizon, n_days)
            execution_date = dates[t + 1]
            exit_date = dates[end - 1]
            score_map = (
                {symbols[i]: float(scores[i]) for i in np.where(~np.isnan(scores))[0]}
                if scores is not None
                else {}
            )
            target = WalkForwardTarget(
                signal_date=dates[t],
                execution_date=execution_date,
                exit_date=exit_date,
                target_symbols=tuple(picks),
                scores=score_map,
                factor_ids=tuple(str(f.get("id")) for f in factors),
                factor_count=factor_count,
                research_run_id=None,
                window_data_version=window.data_version,
                window_snapshot_id=window.snapshot_id,
            )
            targets_by_execution_date[execution_date] = target

            per_window.append({
                "signal_date": dates[t],
                "rebalance_date": dates[t],
                "execution_date": execution_date,
                "scheduled_exit_date": exit_date,
                "window_start": window.dates[0],
                "window_end": window.dates[-1],
                "window_data_version": window.data_version,
                "window_snapshot_id": window.snapshot_id,
                "parent_data_version": parent_data_version,
                "window_return": None,
                "benchmark_return": None,
                "excess_return": None,
                "hit": None,
                "factor_count": factor_count,
                "factor_ids": [f.get("id") for f in factors],
                "accepted_count": len(mining.get("accepted") or []),
                "picks": picks,
            })

    first_execution_idx = min(dates.index(item.execution_date) for item in targets_by_execution_date.values())
    wf_dates = list(dates[first_execution_idx:])
    score_panel = np.full((len(symbols), len(wf_dates)), np.nan)
    symbol_index = {symbol: idx for idx, symbol in enumerate(symbols)}
    cfg = get_research_config().walkforward
    gap_policy = cfg.gap_policy
    overlap_policy = cfg.overlapping_target_policy
    sorted_targets = sorted(targets_by_execution_date.values(), key=lambda item: item.execution_date)
    active_target: WalkForwardTarget | None = None
    target_schedule: list[dict] = []
    state_events: list[dict] = []
    for local_idx, trade_date in enumerate(wf_dates):
        target = targets_by_execution_date.get(trade_date)
        if target is not None:
            if active_target is not None and trade_date <= active_target.exit_date:
                state_events.append({"date": trade_date, "event": "TARGET_REPLACED"})
                if overlap_policy != "replace":
                    target = active_target
            else:
                state_events.append({"date": trade_date, "event": "TARGET_ACTIVATED"})
            active_target = target
        if active_target is not None and trade_date > active_target.exit_date:
            state_events.append({"date": trade_date, "event": "TARGET_EXPIRED"})
            if gap_policy == "cash":
                active_target = None
                state_events.append({"date": trade_date, "event": "MOVED_TO_CASH"})
        if active_target is None:
            continue
        for rank, symbol in enumerate(active_target.target_symbols):
            idx = symbol_index.get(symbol)
            if idx is not None:
                score_panel[idx, local_idx] = len(active_target.target_symbols) - rank
    target_schedule = [
        {
            "signal_date": item.signal_date,
            "execution_date": item.execution_date,
            "exit_date": item.exit_date,
            "target_symbols": list(item.target_symbols),
            "factor_ids": list(item.factor_ids),
            "factor_count": item.factor_count,
            "window_data_version": item.window_data_version,
            "window_snapshot_id": item.window_snapshot_id,
        }
        for item in sorted_targets
    ]

    date_slice = slice(first_execution_idx, n_days)
    continuous = run_topk_backtest(
        score_panel,
        panel["open"][:, date_slice],
        panel["high"][:, date_slice],
        panel["low"][:, date_slice],
        closes[:, date_slice],
        panel["volume"][:, date_slice],
        symbols,
        wf_dates,
        rebalance_interval=1,
        top_k=resolved_top_k,
        initial_cash=1_000_000.0,
        allow_unsafe_without_metadata=True,
        security_meta=np.asarray(security_meta, dtype=object)[:, date_slice] if security_meta is not None else None,
        upper_limit_prices=np.asarray(upper_limit_prices, dtype=object)[:, date_slice] if upper_limit_prices is not None else None,
        lower_limit_prices=np.asarray(lower_limit_prices, dtype=object)[:, date_slice] if lower_limit_prices is not None else None,
    )

    equity_curve = continuous["equity_curve"]
    benchmark_curve = continuous["benchmark_curve"]
    all_trades = continuous["trades"]
    all_turnover = continuous["daily_turnover"]
    date_to_local = {date_value: idx for idx, date_value in enumerate(wf_dates)}
    for item in per_window:
        start_idx = date_to_local.get(item["execution_date"])
        end_idx = date_to_local.get(item["scheduled_exit_date"])
        if start_idx is None or end_idx is None or end_idx <= start_idx:
            window_return = 0.0
            bench_return = 0.0
        else:
            window_return = equity_curve[end_idx] / equity_curve[start_idx] - 1
            bench_return = benchmark_curve[end_idx] / benchmark_curve[start_idx] - 1
        excess = window_return - bench_return
        item["window_return"] = round(window_return, 4)
        item["benchmark_return"] = round(bench_return, 4)
        item["excess_return"] = round(excess, 4)
        item["hit"] = bool(excess > 0)
        item["price_limit_meta_coverage"] = continuous.get("price_limit_meta_coverage")
        item["price_limit_buy_meta_coverage"] = continuous.get("price_limit_buy_meta_coverage")
        item["price_limit_sell_meta_coverage"] = continuous.get("price_limit_sell_meta_coverage")
        item["price_limit_fallback_count"] = continuous.get("price_limit_fallback_count")
        item["quality_flags"] = continuous.get("price_limit_quality_flags") or []

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
        "mode": "continuous_walkforward",
        "status": "VALID",
        "equity_curve": equity_curve,
        "benchmark_curve": benchmark_curve,
        "dates": wf_dates,
        "metrics": metrics,
        "window_hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
        "target_schedule": target_schedule,
        "per_window": per_window,
        "diagnostics": {
            "research_window_days": window_days,
            "minimum_research_days": research_requirement.minimum_required_days,
            "rebalance_step_days": DEFAULT_STEP_DAYS,
            "holding_horizon_days": horizon,
            "gap_policy": gap_policy,
            "overlap_policy": overlap_policy,
            "continuous_calendar": True,
            "portfolio_benchmark_independent": True,
            "state_events": state_events,
            "observation_count": len(wf_dates),
            "first_date": wf_dates[0] if wf_dates else None,
            "last_date": wf_dates[-1] if wf_dates else None,
        },
        "warning": "; ".join(warnings) if warnings else None,
        "disclaimer": DISCLAIMER,
    }


def _score_metadata(signal_date: str, execution_dates: list[str]) -> list[dict]:
    rows = []
    for execution_date in execution_dates:
        rows.append(
            {
                "feature_time": f"{signal_date}T15:00:00",
                "available_at": f"{signal_date}T15:05:00",
                "executable_from": f"{execution_date}T09:30:00",
                "data_snapshot_id": f"factor_walkforward:{signal_date}",
                "algorithm_version": "factor_walkforward_v1",
            }
        )
    return rows


__all__ = [
    "run_walkforward",
    "default_rebalance_points",
    "DISCLAIMER",
    "WalkForwardWindow",
    "_build_walkforward_window",
]

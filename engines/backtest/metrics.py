from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from engines.risk.drawdown import max_drawdown

TRADING_DAYS_PER_YEAR = 252


def calc_backtest_metrics(equity_curve: Sequence[float], trades: Sequence[dict] | None = None) -> dict:
    if len(equity_curve) < 2:
        return {"total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0, "win_rate": None}
    total_return = equity_curve[-1] / equity_curve[0] - 1
    trades = trades or []
    closed = [item for item in trades if "pnl" in item]
    wins = [item for item in closed if item["pnl"] > 0]
    return {
        "total_return": round(total_return, 4),
        "max_drawdown": max_drawdown(equity_curve),
        "trade_count": len(trades),
        "win_rate": round(len(wins) / len(closed), 4) if closed else None,
    }


def _annualized_return(total_return: float, n_days: int) -> float:
    """按 252 个交易日年化。"""
    if n_days <= 0 or total_return <= -1:
        return 0.0
    return (1 + total_return) ** (TRADING_DAYS_PER_YEAR / n_days) - 1


def _monthly_returns(values: Sequence[float], dates: Sequence) -> dict:
    """按月分组计算月收益：当月最后一个值 / 上月最后一个值 - 1。"""
    month_last: dict[str, float] = {}
    for d, v in zip(dates, values):
        month_last[str(d)[:7]] = float(v)
    months = sorted(month_last)
    result: dict[str, float] = {}
    prev_value = float(values[0])
    for month in months:
        cur = month_last[month]
        result[month] = round(cur / prev_value - 1, 4) if prev_value else 0.0
        prev_value = cur
    return result


def _round_trip_win_rate(trades: Sequence[dict]) -> float | None:
    """按标的 FIFO 配对买卖计算往返胜率（含交易成本）。"""
    buy_queues: dict[str, list[list[float]]] = {}  # symbol -> [[shares, total_cost], ...]
    closed = 0
    wins = 0
    for trade in trades:
        symbol = trade.get("symbol")
        side = trade.get("side")
        shares = float(trade.get("shares", 0))
        value = float(trade.get("value", 0))
        cost = float(trade.get("cost", 0))
        if side == "buy":
            buy_queues.setdefault(symbol, []).append([shares, value + cost])
        elif side == "sell":
            queue = buy_queues.get(symbol) or []
            remaining = shares
            buy_total = 0.0
            while remaining > 1e-9 and queue:
                lot_shares, lot_cost = queue[0]
                take = min(lot_shares, remaining)
                portion = lot_cost * (take / lot_shares)
                buy_total += portion
                queue[0] = [lot_shares - take, lot_cost - portion]
                remaining -= take
                if queue[0][0] <= 1e-9:
                    queue.pop(0)
            sold = shares - remaining
            if sold > 1e-9:
                pnl = value * (sold / shares) - cost - buy_total
                closed += 1
                wins += 1 if pnl > 0 else 0
    return round(wins / closed, 4) if closed else None


def calc_portfolio_metrics(
    equity_curve: Sequence[float],
    benchmark_curve: Sequence[float] | None = None,
    trades: Sequence[dict] | None = None,
    daily_turnover: Sequence[float] | None = None,
    dates: Sequence | None = None,
) -> dict:
    """组合回测指标：收益/风险/超额/月度收益/换手/胜率（无风险利率取 0）。"""
    equity = np.asarray(list(equity_curve), dtype=float)
    if equity.size < 2 or equity[0] <= 0:
        return {
            "total_return": 0.0, "annual_return": 0.0, "annual_vol": 0.0,
            "sharpe": 0.0, "max_drawdown": 0.0, "excess_annual_return": None,
            "monthly_returns": {}, "avg_daily_turnover": None,
            "trade_count": 0, "win_rate": None,
        }
    rets = equity[1:] / equity[:-1] - 1
    n_days = int(rets.size)
    total_return = float(equity[-1] / equity[0] - 1)
    annual_return = _annualized_return(total_return, n_days)
    ret_std = float(rets.std(ddof=1)) if n_days > 1 else 0.0
    annual_vol = ret_std * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = float(rets.mean() / ret_std * np.sqrt(TRADING_DAYS_PER_YEAR)) if ret_std > 0 else 0.0

    excess = None
    if benchmark_curve is not None:
        bench = np.asarray(list(benchmark_curve), dtype=float)
        if bench.size >= 2 and bench[0] > 0:
            bench_annual = _annualized_return(float(bench[-1] / bench[0] - 1), bench.size - 1)
            excess = round(annual_return - bench_annual, 4)

    return {
        "total_return": round(total_return, 4),
        "annual_return": round(annual_return, 4),
        "annual_vol": round(annual_vol, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": max_drawdown(list(equity)),
        "excess_annual_return": excess,
        "monthly_returns": _monthly_returns(equity, dates) if dates else {},
        "avg_daily_turnover": round(float(np.mean(list(daily_turnover))), 4) if daily_turnover else None,
        "trade_count": len(trades) if trades else 0,
        "win_rate": _round_trip_win_rate(trades or []),
    }


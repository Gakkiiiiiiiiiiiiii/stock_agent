from __future__ import annotations

from collections.abc import Sequence

from engines.backtest.metrics import calc_backtest_metrics


def buy_and_hold_backtest(closes: Sequence[float], initial_cash: float = 100000.0) -> dict:
    if not closes:
        return {"equity_curve": [], "metrics": calc_backtest_metrics([])}
    shares = initial_cash / closes[0]
    equity_curve = [shares * close for close in closes]
    return {"equity_curve": equity_curve, "metrics": calc_backtest_metrics(equity_curve)}


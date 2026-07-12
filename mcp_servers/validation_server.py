from __future__ import annotations

from engines.backtest.backtest_runner import buy_and_hold_backtest


def walk_forward_validate(closes: list[float], initial_cash: float = 100000.0) -> dict:
    result = buy_and_hold_backtest(closes=closes, initial_cash=initial_cash)
    return {"validation": result["metrics"], "equity_curve_length": len(result["equity_curve"])}


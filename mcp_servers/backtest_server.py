from __future__ import annotations

from engines.backtest.backtest_runner import buy_and_hold_backtest
from engines.backtest.reports import render_backtest_report


def run_buy_and_hold_backtest(closes: list[float], initial_cash: float = 100000.0) -> dict:
    return buy_and_hold_backtest(closes, initial_cash)


def render_report(result: dict) -> dict:
    return {"markdown": render_backtest_report(result)}


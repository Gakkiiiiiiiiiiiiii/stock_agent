from __future__ import annotations

from collections.abc import Sequence

from engines.risk.drawdown import max_drawdown


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


from __future__ import annotations

from datetime import date

from engines.market.trading_calendar import advance_trading_days
from mcp_servers.market_data_server import get_kline


class DecisionOutcomeEvaluator:
    """Outcome evaluator with a NEXT_SESSION_OPEN anchor to prevent look-ahead."""

    def evaluate(self, decision: dict, evaluation_date: date) -> dict:
        candidates = [item for item in (decision.get("candidates") or []) if isinstance(item, dict) and item.get("symbol")]
        if not candidates:
            raise ValueError("DECISION_HAS_NO_SYMBOL_CANDIDATES")
        as_of = decision.get("decision_as_of") or decision.get("created_at")
        decision_date = as_of.date() if hasattr(as_of, "date") else date.fromisoformat(str(as_of)[:10])
        anchor = str(decision.get("evaluation_anchor") or "NEXT_SESSION_OPEN")
        entry_date = advance_trading_days(decision_date, 1) if anchor == "NEXT_SESSION_OPEN" else decision_date
        component_metrics = [self._symbol_return(str(item["symbol"]), entry_date, evaluation_date) for item in candidates]
        benchmark_symbol = str(decision.get("benchmark_symbol") or "000001.SH")
        benchmark_return, benchmark_metric = self._symbol_return(benchmark_symbol, entry_date, evaluation_date)
        portfolio = sum(item[0] for item in component_metrics) / len(component_metrics)
        return {
            "benchmark_return": benchmark_return,
            "portfolio_return": portfolio,
            "realized_metrics": {
                "evaluation_anchor": anchor,
                "entry_date": entry_date.isoformat(),
                "exit_date": evaluation_date.isoformat(),
                "components": [{"symbol": candidate["symbol"], **metric} for candidate, (_return, metric) in zip(candidates, component_metrics)],
                "benchmark": {"symbol": benchmark_symbol, **benchmark_metric},
            },
        }

    @staticmethod
    def _symbol_return(symbol: str, entry_date: date, exit_date: date) -> tuple[float, dict]:
        result = get_kline(symbol=symbol, start_date=entry_date.isoformat(), end_date=exit_date.isoformat(), freq="1d")
        rows = result.get("records") or result.get("data") or result.get("rows") or result.get("kline") or []
        rows = sorted((row for row in rows if isinstance(row, dict)), key=lambda row: str(row.get("date") or row.get("trading_date") or ""))
        if not rows or rows[0].get("open") is None or rows[-1].get("close") is None:
            raise ValueError(f"INSUFFICIENT_PRICE_DATA:{symbol}")
        entry_price, exit_price = float(rows[0]["open"]), float(rows[-1]["close"])
        if entry_price <= 0:
            raise ValueError(f"INVALID_ENTRY_PRICE:{symbol}")
        return exit_price / entry_price - 1, {"entry": {"date": str(rows[0].get("date") or entry_date), "price_type": "OPEN", "price": entry_price}, "exit": {"date": str(rows[-1].get("date") or exit_date), "price_type": "CLOSE", "price": exit_price}}

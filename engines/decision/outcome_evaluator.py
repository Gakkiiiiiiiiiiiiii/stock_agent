from __future__ import annotations

from datetime import date

from mcp_servers.market_data_server import get_kline


class DecisionOutcomeEvaluator:
    """Evaluates equal-weight candidate performance from the recorded decision evidence."""

    def evaluate(self, decision: dict, evaluation_date: date) -> dict:
        candidates = [item for item in (decision.get("candidates") or []) if isinstance(item, dict) and item.get("symbol")]
        if not candidates:
            raise ValueError("DECISION_HAS_NO_SYMBOL_CANDIDATES")
        returns = [self._symbol_return(str(item["symbol"]), decision["created_at"].date(), evaluation_date) for item in candidates]
        benchmark = self._symbol_return("000001.SH", decision["created_at"].date(), evaluation_date)
        portfolio = sum(returns) / len(returns)
        return {"benchmark_return": benchmark, "portfolio_return": portfolio, "realized_metrics": {"symbols": [item["symbol"] for item in candidates], "component_returns": returns}}

    @staticmethod
    def _symbol_return(symbol: str, start: date, end: date) -> float:
        result = get_kline(symbol=symbol, start_date=start.isoformat(), end_date=end.isoformat(), freq="1d")
        rows = result.get("data") or result.get("rows") or result.get("kline") or []
        closes = [float(row.get("close")) for row in rows if isinstance(row, dict) and row.get("close") is not None]
        if len(closes) < 2:
            raise ValueError(f"INSUFFICIENT_PRICE_DATA:{symbol}")
        return closes[-1] / closes[0] - 1

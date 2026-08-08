from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from engines.market.market_clock import MarketClock
from engines.market.trading_calendar import advance_trading_days
from mcp_servers.market_data_server import get_kline


class PriceObservation(BaseModel):
    requested_date: date
    actual_date: date
    price_type: str
    price: float
    tradable: bool = True
    quality_flags: list[str] = Field(default_factory=list)


class DecisionOutcomeEvaluator:
    """Outcome evaluator with a NEXT_SESSION_OPEN anchor to prevent look-ahead."""

    def evaluate(self, decision: dict, evaluation_date: date) -> dict:
        candidates = [item for item in (decision.get("candidates") or []) if isinstance(item, dict) and item.get("symbol")]
        if not candidates:
            raise ValueError("DECISION_HAS_NO_SYMBOL_CANDIDATES")
        as_of = decision.get("decision_as_of") or decision.get("created_at")
        decision_date = self._market_date(as_of)
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

    @classmethod
    def _symbol_return(cls, symbol: str, entry_date: date, exit_date: date) -> tuple[float, dict]:
        result = get_kline(symbol=symbol, start_date=entry_date.isoformat(), end_date=exit_date.isoformat(), freq="1d")
        rows = result.get("records") or result.get("data") or result.get("rows") or result.get("kline") or []
        dated_rows = sorted(
            ((cls._row_date(row), row) for row in rows if isinstance(row, dict) and cls._row_date(row) is not None),
            key=lambda item: item[0],
        )
        entry_row = next((row for row_date, row in dated_rows if row_date == entry_date and row.get("open") is not None), None)
        if entry_row is None:
            raise ValueError(f"ENTRY_NOT_TRADABLE:{symbol}:{entry_date.isoformat()}")
        exact_exit = next((row for row_date, row in reversed(dated_rows) if row_date == exit_date and row.get("close") is not None), None)
        fallback_exit = next((item for item in reversed(dated_rows) if item[0] <= exit_date and item[1].get("close") is not None), None)
        if exact_exit is None and fallback_exit is None:
            raise ValueError(f"EXIT_PRICE_UNAVAILABLE:{symbol}:{exit_date.isoformat()}")
        exit_date_actual, exit_row = (exit_date, exact_exit) if exact_exit is not None else fallback_exit
        entry_price, exit_price = float(entry_row["open"]), float(exit_row["close"])
        if entry_price <= 0:
            raise ValueError(f"INVALID_ENTRY_PRICE:{symbol}")
        entry = PriceObservation(requested_date=entry_date, actual_date=entry_date, price_type="OPEN", price=entry_price)
        exit = PriceObservation(
            requested_date=exit_date,
            actual_date=exit_date_actual,
            price_type="CLOSE",
            price=exit_price,
            tradable=exact_exit is not None,
            quality_flags=[] if exact_exit is not None else ["EXIT_SESSION_NO_QUOTE", "MARK_TO_LAST_CLOSE"],
        )
        return exit_price / entry_price - 1, {"entry": entry.model_dump(mode="json"), "exit": exit.model_dump(mode="json")}

    @staticmethod
    def _row_date(row: dict) -> date | None:
        raw = row.get("date") or row.get("trading_date") or row.get("trade_date") or row.get("time")
        if raw is None:
            return None
        text = str(raw)
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8])) if text[:8].isdigit() else date.fromisoformat(text[:10])
        except ValueError:
            return None

    @staticmethod
    def _market_date(value) -> date:
        if isinstance(value, date) and not hasattr(value, "hour"):
            return value
        parsed = value if hasattr(value, "tzinfo") else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return MarketClock().calendar_date(parsed)

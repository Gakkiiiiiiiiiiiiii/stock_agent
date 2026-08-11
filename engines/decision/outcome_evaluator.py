"""决策结果评估 v2：多维基准对比 + 路径风险指标。

相对 v1 的扩展：
- 通过 BenchmarkRouter 按决策属性（sector/style/decision_type/theme/market）路由基准，
  除市场主基准外还计算 style / sector / theme 三条基准腿的相对超额；
- 新增路径指标：候选等权组合在持有期内的 max_drawdown / MAE / MFE（基于日收盘归一化净值）；
- 向后兼容：portfolio_return == absolute_return，benchmark_return == market_return，
  excess_return == market_excess_return；
- 基准腿容错：任一基准行情不可得时该腿指标置 None 并在 realized_metrics.flags 中记录，
  绝不因基准缺失导致评估崩溃（候选标的本身无入场价仍抛 ENTRY_NOT_TRADABLE）。

theme 基准腿：当决策带 themes 时路由输出 THEME_BASKET（决策候选等权组合）。当前组合
口径同为候选等权，因此 theme_basket_return == absolute_return、theme_excess_return == 0，
该腿在组合加权（非等权）生效后用于度量主动权重偏离主题篮子的贡献。
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from engines.decision.benchmark_router import THEME_BASKET, BenchmarkRouter
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

    def __init__(self, benchmark_router: BenchmarkRouter | None = None) -> None:
        self.benchmark_router = benchmark_router or BenchmarkRouter()

    def evaluate(self, decision: dict, evaluation_date: date) -> dict:
        candidates = [item for item in (decision.get("candidates") or []) if isinstance(item, dict) and item.get("symbol")]
        if not candidates:
            raise ValueError("DECISION_HAS_NO_SYMBOL_CANDIDATES")
        as_of = decision.get("decision_as_of") or decision.get("created_at")
        decision_date = self._market_date(as_of)
        anchor = str(decision.get("evaluation_anchor") or "NEXT_SESSION_OPEN")
        entry_date = advance_trading_days(decision_date, 1) if anchor == "NEXT_SESSION_OPEN" else decision_date
        windows = [self._symbol_window(str(item["symbol"]), entry_date, evaluation_date) for item in candidates]
        component_metrics = [(window[0], window[1]) for window in windows]
        absolute_return = sum(item[0] for item in component_metrics) / len(component_metrics)

        stored_route = decision.get("benchmark_route")
        if isinstance(stored_route, dict) and stored_route.get("primary_benchmark"):
            route = dict(stored_route)
        else:
            route = self.benchmark_router.route(
                {
                    "decision_type": decision.get("decision_type"),
                    "symbols": [str(item["symbol"]) for item in candidates],
                    "themes": list(decision.get("themes") or []),
                    "market": decision.get("market") or "CN_A",
                    "style": decision.get("style"),
                    "sector": decision.get("sector"),
                }
            )
        explicit = decision.get("benchmark_symbol")
        if explicit:
            if str(explicit) != route["primary_benchmark"]:
                route["reason"] = f"{route['reason']} | explicit benchmark_symbol 覆盖主基准→{explicit}"
            route["primary_benchmark"] = str(explicit)

        flags: list[str] = []
        market_return, market_metric = self._benchmark_leg(route["primary_benchmark"], entry_date, evaluation_date, flags, "MARKET_BENCHMARK_UNAVAILABLE")
        style_return, style_metric = self._benchmark_leg(route.get("style_benchmark"), entry_date, evaluation_date, flags, "STYLE_BENCHMARK_UNAVAILABLE")
        sector_return, sector_metric = self._benchmark_leg(route.get("sector_benchmark"), entry_date, evaluation_date, flags, "SECTOR_BENCHMARK_UNAVAILABLE")
        if route.get("theme_benchmark") == THEME_BASKET:
            theme_basket_return = absolute_return
            flags.append("THEME_BASKET_PROXY_EQUAL_WEIGHT_CANDIDATES")
        else:
            theme_basket_return = None

        drawdown, mae, mfe = self._path_metrics([window[2] for window in windows], entry_date, evaluation_date)

        def excess(leg: float | None) -> float | None:
            return absolute_return - leg if leg is not None else None

        return {
            "absolute_return": absolute_return,
            "portfolio_return": absolute_return,
            "market_return": market_return,
            "benchmark_return": market_return,
            "market_excess_return": excess(market_return),
            "excess_return": excess(market_return),
            "style_return": style_return,
            "style_excess_return": excess(style_return),
            "sector_return": sector_return,
            "sector_excess_return": excess(sector_return),
            "theme_basket_return": theme_basket_return,
            "theme_excess_return": excess(theme_basket_return),
            "max_drawdown": drawdown,
            "max_adverse_excursion": mae,
            "max_favorable_excursion": mfe,
            "benchmark_route": route,
            "realized_metrics": {
                "evaluation_anchor": anchor,
                "entry_date": entry_date.isoformat(),
                "exit_date": evaluation_date.isoformat(),
                "components": [{"symbol": candidate["symbol"], **metric} for candidate, (_return, metric) in zip(candidates, component_metrics)],
                "benchmark": {"symbol": route["primary_benchmark"], **(market_metric or {"unavailable": True})},
                "style_benchmark": {"symbol": route.get("style_benchmark"), **(style_metric or {})} if route.get("style_benchmark") else None,
                "sector_benchmark": {"symbol": route.get("sector_benchmark"), **(sector_metric or {})} if route.get("sector_benchmark") else None,
                "flags": flags,
            },
        }

    def _benchmark_leg(self, symbol: str | None, entry_date: date, exit_date: date, flags: list[str], flag: str) -> tuple[float | None, dict | None]:
        if not symbol or symbol == THEME_BASKET:
            return None, None
        try:
            leg_return, metric, _rows = self._symbol_window(symbol, entry_date, exit_date)
            return leg_return, metric
        except Exception:  # noqa: BLE001 — 基准腿缺失不应中断评估
            flags.append(flag)
            return None, None

    @classmethod
    def _symbol_window(cls, symbol: str, entry_date: date, exit_date: date) -> tuple[float, dict, list[tuple[date, dict]]]:
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
        return exit_price / entry_price - 1, {"entry": entry.model_dump(mode="json"), "exit": exit.model_dump(mode="json")}, dated_rows

    @staticmethod
    def _path_metrics(windows: list[list[tuple[date, dict]]], entry_date: date, exit_date: date) -> tuple[float | None, float | None, float | None]:
        """候选等权组合归一化净值路径 → (max_drawdown, MAE, MFE)，基于日收盘。"""
        series: list[dict[date, float]] = []
        for dated_rows in windows:
            entry_row = next((row for row_date, row in dated_rows if row_date == entry_date and row.get("open") is not None), None)
            if entry_row is None:
                return None, None, None
            base = float(entry_row["open"])
            points = {entry_date: 1.0}
            for row_date, row in dated_rows:
                if entry_date <= row_date <= exit_date and row.get("close") is not None:
                    points[row_date] = float(row["close"]) / base
            series.append(points)
        if not series:
            return None, None, None
        all_dates = sorted({day for points in series for day in points})
        basket: list[float] = []
        for day in all_dates:
            values = []
            for points in series:
                known = [d for d in points if d <= day]
                values.append(points[max(known)] if known else 1.0)
            basket.append(sum(values) / len(values))
        peak = basket[0]
        max_drawdown = 0.0
        for value in basket:
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, value / peak - 1)
        return max_drawdown, min(basket) - 1, max(basket) - 1

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

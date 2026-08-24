from __future__ import annotations

import os
from typing import Any, Protocol

from clients._http import SubsystemHttpClient
from app.model_gateway.metrics import TraceContext


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    if isinstance(value, dict):
        merged = dict(value)
        for key in ("contract_version", "service_version", "snapshot_id", "as_of", "available_at"):
            if key in payload:
                merged.setdefault(key, payload[key])
        return merged
    return payload


class QuantClient(Protocol):
    def get_bars(self, symbols: list[str], start: str, end: str, *, adjust: str = "qfq", trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def get_market_snapshot(self, snapshot_id: str = "latest") -> dict[str, Any]: ...
    def get_market_features(self, symbol: str, start: str, end: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def get_market_regime(self, *, as_of: str | None = None) -> dict[str, Any]: ...
    def get_market_regime_history(self, *, start: str | None = None, end: str | None = None, limit: int = 100) -> dict[str, Any]: ...
    def get_sector_strength(self, *, as_of: str | None = None, limit: int = 20) -> dict[str, Any]: ...
    def get_technical_evidence(self, symbol: str, *, as_of: str | None = None) -> dict[str, Any]: ...
    def get_portfolio_snapshot(self, account_id: str | None = None) -> dict[str, Any]: ...
    def get_portfolio_risk_inputs(self, account_id: str | None = None) -> dict[str, Any]: ...
    def get_execution_status(self, account_id: str | None = None) -> dict[str, Any]: ...
    def get_security_status(self, symbol: str, start: str, end: str) -> dict[str, Any]: ...
    def get_backtest(self, backtest_id: str) -> dict[str, Any]: ...
    def get_backtest_metrics(self, backtest_id: str) -> dict[str, Any]: ...
    def get_backtest_trades(self, backtest_id: str) -> dict[str, Any]: ...
    def get_trading_calendar(self, start: str, end: str, *, market_code: str = "CN_A", trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]: ...


class RemoteQuantClient(SubsystemHttpClient):
    """quant HTTP client（集成文档 §85 / 收尾文档 §28）：
    market-data.v1 / backtest.v1 / trading.v1 消费者。

    默认地址：quant（§12/§65，8011）。agent 只通过 HTTP 契约依赖 quant（§6.3）。
    """

    def __init__(self, base_url: str | None = None, *, timeout_seconds: float = 30.0, retries: int = 2, **http_kwargs) -> None:
        super().__init__(
            base_url or os.getenv("QUANT_SERVICE_URL") or os.getenv("MARKET_DATA_SERVICE_URL", "http://quant:8011"),
            timeout_seconds=timeout_seconds,
            retries=retries,
            **http_kwargs,
        )

    def get_bars(self, symbols: list[str], start: str, end: str, *, adjust: str = "qfq", trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"symbols": symbols, "start": start, "end": end, "adjust": adjust}
        return _data(self.request("POST", "/api/v1/market/bars/batch", payload=payload, trace=trace, trace_context=trace_context))

    def get_market_snapshot(self, snapshot_id: str = "latest", *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/market/snapshots/{snapshot_id}", trace=trace, trace_context=trace_context, contract_version="market-data.v1", expected_snapshot_id=None if snapshot_id == "latest" else snapshot_id))

    def get_market_features(self, symbol: str, start: str, end: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        # PIT 元数据（§77）：security_status_daily（停牌/ST/退市状态）。
        return self.request("GET", "/api/v1/market/security-status", params={"symbol": symbol, "start": start, "end": end}, trace=trace, trace_context=trace_context)

    def get_security_status(self, symbol: str, start: str, end: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.get_market_features(symbol, start, end, trace=trace, trace_context=trace_context)

    def get_market_regime(self, *, as_of: str | None = None, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("GET", "/api/v1/market/regime", params={"as_of": as_of} if as_of else None, trace=trace, trace_context=trace_context, contract_version="market-data.v1"))

    def get_market_regime_history(self, *, start: str | None = None, end: str | None = None, limit: int = 100, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return _data(self.request("GET", "/api/v1/market/regime/history", params=params, trace=trace, trace_context=trace_context))

    def get_sector_strength(self, *, as_of: str | None = None, limit: int = 20, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if as_of:
            params["as_of"] = as_of
        return _data(self.request("GET", "/api/v1/market/sectors/strength", params=params, trace=trace, trace_context=trace_context, contract_version="market-data.v1"))

    def get_technical_evidence(self, symbol: str, *, as_of: str | None = None, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol}
        if as_of:
            params["as_of"] = as_of
        return _data(self.request("GET", "/api/v1/market/technical-evidence", params=params, trace=trace, trace_context=trace_context, contract_version="market-data.v1"))

    def get_portfolio_snapshot(self, account_id: str | None = None, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("GET", "/api/v1/portfolio/snapshot", params={"account_id": account_id} if account_id else None, trace=trace, trace_context=trace_context, contract_version="market-data.v1"))

    def get_portfolio_risk_inputs(self, account_id: str | None = None, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("GET", "/api/v1/portfolio/risk-inputs", params={"account_id": account_id} if account_id else None, trace=trace, trace_context=trace_context, contract_version="market-data.v1"))

    def get_execution_status(self, account_id: str | None = None, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("GET", "/api/v1/execution/status", params={"account_id": account_id} if account_id else None, trace=trace, trace_context=trace_context))

    def get_backtest(self, backtest_id: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/backtests/{backtest_id}", trace=trace, trace_context=trace_context, contract_version="backtest.v1"))

    def get_backtest_metrics(self, backtest_id: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/backtests/{backtest_id}/metrics", trace=trace, trace_context=trace_context, contract_version="backtest.v1"))

    def get_backtest_trades(self, backtest_id: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/backtests/{backtest_id}/trades", trace=trace, trace_context=trace_context, contract_version="backtest.v1"))

    def get_trading_calendar(self, start: str, end: str, *, market_code: str = "CN_A", trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only market calendar contract; quant remains calendar authority."""
        return _data(self.request("GET", "/api/v1/market/calendar", params={"start": start, "end": end, "market_code": market_code}, trace=trace, trace_context=trace_context, contract_version="market-calendar.v1"))

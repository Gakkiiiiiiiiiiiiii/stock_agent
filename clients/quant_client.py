from __future__ import annotations

import os
from typing import Any, Protocol

from clients._http import SubsystemHttpClient


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else payload


class QuantClient(Protocol):
    def get_bars(self, symbols: list[str], start: str, end: str, *, adjust: str = "qfq") -> dict[str, Any]: ...
    def create_market_snapshot(self, symbols: list[str], start: str, end: str, *, adjust: str = "qfq") -> dict[str, Any]: ...
    def get_market_snapshot(self, snapshot_id: str) -> dict[str, Any]: ...
    def get_market_features(self, symbol: str, start: str, end: str) -> dict[str, Any]: ...
    def get_security_status(self, symbol: str, start: str, end: str) -> dict[str, Any]: ...
    def create_backtest(self, config: dict[str, Any]) -> dict[str, Any]: ...
    def get_backtest(self, backtest_id: str) -> dict[str, Any]: ...
    def get_backtest_metrics(self, backtest_id: str) -> dict[str, Any]: ...
    def get_backtest_trades(self, backtest_id: str) -> dict[str, Any]: ...
    def run_paper(self, account_id: str, as_of: str, market_prices: dict | None = None) -> dict[str, Any]: ...
    def get_paper_state(self, account_id: str) -> dict[str, Any]: ...


class RemoteQuantClient(SubsystemHttpClient):
    """quant HTTP client（集成文档 §85 / 收尾文档 §28）：
    market-data.v1 / backtest.v1 / trading.v1 消费者。

    默认地址：quant（§12/§65，8011）。agent 只通过 HTTP 契约依赖 quant（§6.3）。
    """

    def __init__(self, base_url: str | None = None, *, timeout_seconds: float = 30.0, retries: int = 2) -> None:
        super().__init__(
            base_url or os.getenv("QUANT_SERVICE_URL") or os.getenv("MARKET_DATA_SERVICE_URL", "http://quant:8011"),
            timeout_seconds=timeout_seconds,
            retries=retries,
        )

    def get_bars(self, symbols: list[str], start: str, end: str, *, adjust: str = "qfq") -> dict[str, Any]:
        payload = {"symbols": symbols, "start": start, "end": end, "adjust": adjust}
        return _data(self.request("POST", "/api/v1/market/bars/batch", payload=payload))

    def create_market_snapshot(self, symbols: list[str], start: str, end: str, *, adjust: str = "qfq") -> dict[str, Any]:
        # 收尾文档 §10：显式创建不可变市场快照。
        payload = {"symbols": symbols, "start": start, "end": end, "adjust": adjust}
        return _data(self.request("POST", "/api/v1/market/snapshots", payload=payload))

    def get_market_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/market/snapshots/{snapshot_id}"))

    def get_market_features(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        # PIT 元数据（§77）：security_status_daily（停牌/ST/退市状态）。
        return self.request("GET", "/api/v1/market/security-status", params={"symbol": symbol, "start": start, "end": end})

    def get_security_status(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        return self.get_market_features(symbol, start, end)

    def create_backtest(self, config: dict[str, Any]) -> dict[str, Any]:
        return _data(self.request("POST", "/api/v1/backtests", payload=config))

    def get_backtest(self, backtest_id: str) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/backtests/{backtest_id}"))

    def get_backtest_metrics(self, backtest_id: str) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/backtests/{backtest_id}/metrics"))

    def get_backtest_trades(self, backtest_id: str) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/backtests/{backtest_id}/trades"))

    def run_paper(self, account_id: str, as_of: str, market_prices: dict | None = None) -> dict[str, Any]:
        payload = {"account_id": account_id, "as_of": as_of, "market_prices": market_prices or {}}
        return _data(self.request("POST", "/api/v1/paper/run", payload=payload))

    def get_paper_state(self, account_id: str) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/paper/accounts/{account_id}"))

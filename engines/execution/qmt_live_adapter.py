"""Narrow QMT execution adapter. LIVE traffic stays disabled unless explicitly configured."""
from __future__ import annotations

import os

import httpx

from engines.execution.models import ExecutionOrder


class QmtLiveAdapter:
    def __init__(self, base_url: str | None = None, enabled: bool | None = None) -> None:
        self.base_url = (base_url or os.getenv("QMT_EXECUTION_BASE_URL", "")).rstrip("/")
        self.enabled = enabled if enabled is not None else os.getenv("QMT_LIVE_EXECUTION_ENABLED", "false").lower() in {"1", "true", "yes"}

    def _request(self, method: str, path: str, payload: dict | None = None):
        if not self.enabled or not self.base_url:
            raise RuntimeError("QMT_LIVE_EXECUTION_DISABLED")
        response = httpx.request(method, f"{self.base_url}{path}", json=payload, timeout=10)
        response.raise_for_status()
        return response.json()

    def submit(self, order: ExecutionOrder) -> str:
        response = self._request("POST", "/orders", order.model_dump(mode="json"))
        return str(response["broker_order_id"])

    def cancel(self, broker_order_id: str) -> None:
        self._request("POST", f"/orders/{broker_order_id}/cancel")

    def query_orders(self) -> list[dict]: return list(self._request("GET", "/orders") or [])
    def query_fills(self) -> list[dict]: return list(self._request("GET", "/fills") or [])
    def query_positions(self) -> dict: return dict(self._request("GET", "/positions") or {})
    def query_cash(self) -> float: return float((self._request("GET", "/cash") or {}).get("cash", 0))
    def snapshot(self) -> dict: return {"cash": self.query_cash(), "positions": self.query_positions(), "orders": self.query_orders(), "fills": self.query_fills()}

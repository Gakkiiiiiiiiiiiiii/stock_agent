from __future__ import annotations

from typing import Any

import httpx


class SubsystemHttpClient:
    """Small synchronous JSON client used at the stock_agent boundary."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = httpx.request(
            method,
            f"{self.base_url}{path}",
            json=payload,
            params={key: value for key, value in (params or {}).items() if value is not None},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {"items": body}

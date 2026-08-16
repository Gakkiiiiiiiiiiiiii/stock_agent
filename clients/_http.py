from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import httpx


class SubsystemHttpClient:
    """Small synchronous JSON client used at the stock_agent boundary."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 30.0, retries: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        trace_id = str((payload or {}).get("trace_id") or uuid4())
        # §32：统一 Trace Headers，出站调用透传 trace/decision/caller。
        headers = {"X-Trace-Id": trace_id, "X-Caller-Service": "stock_agent"}
        decision_id = (payload or {}).get("decision_id")
        if decision_id:
            headers["X-Decision-Id"] = str(decision_id)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = httpx.request(
                    method,
                    f"{self.base_url}{path}",
                    json=payload,
                    params={key: value for key, value in (params or {}).items() if value is not None},
                    headers=headers,
                    timeout=httpx.Timeout(connect=min(self.timeout_seconds, 5.0), read=self.timeout_seconds, write=self.timeout_seconds, pool=5.0),
                )
                if response.status_code not in {408, 429, 502, 503, 504}:
                    response.raise_for_status()
                    body = response.json()
                    return body if isinstance(body, dict) else {"items": body}
                response.raise_for_status()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in {408, 429, 502, 503, 504}
                if not retryable or attempt >= self.retries:
                    break
                time.sleep(0.15 * (2**attempt))
        raise RuntimeError(f"subsystem request failed path={path} trace_id={trace_id}") from last_error

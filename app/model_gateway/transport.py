from __future__ import annotations

from typing import Any

import httpx

from .retry import RetryableError, parse_retry_after


class OpenAICompatibleTransport:
    """Infrastructure adapter for OpenAI-compatible JSON endpoints.

    Business code only sees ``ModelGateway``; this adapter is the sole place
    where the provider's ``/chat/completions`` path is known.
    """

    def __init__(self, *, base_url: str, api_key: str, http_client: httpx.Client, timeout_seconds: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.http_client = http_client
        self.timeout_seconds = timeout_seconds

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        trace_headers = payload.pop("_trace_headers", {})
        response = self.http_client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", **{str(key): str(value) for key, value in trace_headers.items()}},
            json=payload,
            timeout=self.timeout_seconds,
        )
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise RetryableError(
                f"model provider returned HTTP {response.status_code}",
                status_code=response.status_code,
                retry_after=parse_retry_after(response.headers.get("Retry-After")),
            )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("model provider returned a non-object response")  # noqa: TRY004 - preserve transport exception contract
        return value

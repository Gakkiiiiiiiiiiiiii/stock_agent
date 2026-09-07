"""Narrow authenticated HTTP adapter for the locked content bundle contract."""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from time import sleep
from typing import Any

import httpx

from app.application.knowledge_conclusion.bundle_validator import (
    BundleValidationError,
    ContentKnowledgeBundleValidator,
)
from app.model_gateway.retry import RetryPolicy, parse_retry_after
from app.ports.content_knowledge import (
    ContentKnowledgeBundle,
    KnowledgeBundleRequest,
)

_TRACE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_BUNDLE_ID = re.compile(r"ckb_[a-f0-9]{64}\Z")
_CREATE_PATH = "/v1/content/knowledge-bundles"


class ContentBundleClientError(RuntimeError):
    """Stable, redacted transport failure; it deliberately carries no token/body."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class RemoteContentKnowledgeBundleClient:
    """Fixed-method/path client.  It exposes no generic request/raw-header API."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key_file: str | Path | None = None,
        timeout_seconds: float = 10.0,
        retry_policy: RetryPolicy | None = None,
        request_fn: Callable[..., httpx.Response] | None = None,
        sleeper: Callable[[float], None] = sleep,
        validator: ContentKnowledgeBundleValidator | None = None,
    ) -> None:
        configured = base_url or os.getenv("CONTENT_SERVICE_URL", "http://stock-content:8100")
        parsed = httpx.URL(configured)
        if parsed.scheme not in {"http", "https"} or not parsed.host or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("CONTENT_BASE_URL_INVALID")
        self._base_url = str(parsed).rstrip("/")
        self._api_key_file = Path(api_key_file or os.getenv("CONTENT_SERVICE_API_KEY_FILE", ""))
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=2, base_delay_seconds=0.1, max_delay_seconds=1.0)
        self._request_fn = request_fn or httpx.request
        self._sleeper = sleeper
        self._validator = validator or ContentKnowledgeBundleValidator()

    def create_bundle(self, request: KnowledgeBundleRequest, *, trace: Any) -> ContentKnowledgeBundle:
        payload = self._validator.request_payload(request)
        # The deterministic idempotency key is internal transport metadata; callers cannot set it.
        return self._send("POST", _CREATE_PATH, payload=payload, trace=trace, allow_retry=True, expected=request)

    def get_bundle(self, bundle_id: str, *, trace: Any) -> ContentKnowledgeBundle:
        if not isinstance(bundle_id, str) or not _BUNDLE_ID.fullmatch(bundle_id):
            raise ValueError("CONTENT_BUNDLE_ID_INVALID")
        bundle = self._send("GET", f"{_CREATE_PATH}/{bundle_id}", payload=None, trace=trace, allow_retry=True, expected=None)
        if bundle.bundle_id != bundle_id:
            raise ContentBundleClientError("CONTENT_BUNDLE_TAMPERED")
        return bundle

    def _send(self, method: str, path: str, *, payload: dict[str, Any] | None, trace: Any, allow_retry: bool, expected: KnowledgeBundleRequest | None) -> ContentKnowledgeBundle:
        trace_id = getattr(trace, "trace_id", None)
        if not isinstance(trace_id, str) or not _TRACE.fullmatch(trace_id):
            raise ValueError("CONTENT_TRACE_ID_INVALID")
        token = self._read_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Caller-Service": "stock_agent",
            "X-Trace-Id": trace_id,
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Idempotency-Key"] = "ckb:" + self._validator.request_hash(payload).split(":", 1)[1]
        timeout = httpx.Timeout(connect=min(5.0, self._timeout_seconds), read=self._timeout_seconds, write=self._timeout_seconds, pool=5.0)
        for attempt in range(max(1, self._retry_policy.max_attempts)):
            try:
                response = self._request_fn(method, f"{self._base_url}{path}", json=payload, headers=headers, timeout=timeout)
            except (httpx.TimeoutException, TimeoutError) as exc:
                if allow_retry and attempt + 1 < self._retry_policy.max_attempts:
                    self._sleeper(self._retry_policy.delay(attempt))
                    continue
                raise ContentBundleClientError("CONTENT_DEPENDENCY_TIMEOUT") from exc
            except httpx.HTTPError as exc:
                if allow_retry and attempt + 1 < self._retry_policy.max_attempts:
                    self._sleeper(self._retry_policy.delay(attempt))
                    continue
                raise ContentBundleClientError("CONTENT_DEPENDENCY_UNAVAILABLE") from exc
            if response.status_code in {408, 429, 500, 502, 503, 504} and allow_retry and attempt + 1 < self._retry_policy.max_attempts:
                self._sleeper(self._retry_policy.delay(attempt, parse_retry_after(response.headers.get("Retry-After"))))
                continue
            if not 200 <= response.status_code < 300:
                raise ContentBundleClientError(_status_code(response.status_code), status_code=response.status_code)
            content_type = response.headers.get("Content-Type", "")
            if "application/json" not in content_type.lower():
                raise ContentBundleClientError("CONTENT_CONTRACT_MISMATCH", status_code=response.status_code)
            try:
                body = response.json()
            except ValueError as exc:
                raise ContentBundleClientError("CONTENT_SCHEMA_INVALID", status_code=response.status_code) from exc
            if not isinstance(body, Mapping):
                raise ContentBundleClientError("CONTENT_SCHEMA_INVALID", status_code=response.status_code)
            try:
                return self._validator.validate(body, expected=expected)
            except BundleValidationError as exc:
                raise ContentBundleClientError(exc.code, status_code=response.status_code) from exc
        raise ContentBundleClientError("CONTENT_DEPENDENCY_UNAVAILABLE")

    def _read_token(self) -> str:
        if not str(self._api_key_file) or not self._api_key_file.is_file():
            raise ContentBundleClientError("CONTENT_DEPENDENCY_UNAVAILABLE")
        try:
            token = self._api_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ContentBundleClientError("CONTENT_DEPENDENCY_UNAVAILABLE") from exc
        if not token or "\r" in token or "\n" in token:
            raise ContentBundleClientError("CONTENT_DEPENDENCY_UNAVAILABLE")
        return token


def _status_code(status: int) -> str:
    if status in {401, 403, 429} or status >= 500:
        return "CONTENT_DEPENDENCY_UNAVAILABLE"
    if status == 409:
        return "CONTENT_SNAPSHOT_MISMATCH"
    if status == 415:
        return "CONTENT_CONTRACT_MISMATCH"
    if status == 422:
        return "CONTENT_SCHEMA_INVALID"
    return "CONTENT_DEPENDENCY_UNAVAILABLE"

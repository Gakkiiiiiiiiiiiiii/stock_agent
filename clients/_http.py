from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any, Callable

import httpx

from app.model_gateway.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.model_gateway.metrics import TraceContext
from app.model_gateway.metrics import MetricsRecorder
from app.model_gateway.retry import RetryPolicy, RetryableError, parse_retry_after

DEPENDENCY_TIMEOUT = "DEPENDENCY_TIMEOUT"
DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
STALE_DATA = "STALE_DATA"
INVALID_SNAPSHOT = "INVALID_SNAPSHOT"


class DependencyError(RuntimeError):
    def __init__(self, code: str, message: str, *, dependency: str | None = None, status_code: int | None = None, trace_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.dependency = dependency
        self.status_code = status_code
        self.trace_id = trace_id


@dataclass(frozen=True)
class HttpTimeout:
    connect: float = 5.0
    read: float = 30.0
    write: float = 30.0
    pool: float = 5.0


class SubsystemHttpClient:
    """Read-only HTTP boundary with shared retries, tracing and failure codes."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 30.0, retries: int = 2, retry_policy: RetryPolicy | None = None, sleeper: Callable[[float], None] = sleep, clock: Callable[[], float] = monotonic, request_fn: Callable[..., httpx.Response] | None = None, circuit_breaker: CircuitBreaker | None = None, contract_version: str | None = None, metrics: MetricsRecorder | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=max(1, retries + 1), base_delay_seconds=0.15, max_delay_seconds=8.0)
        self.sleeper = sleeper
        self.clock = clock
        self.request_fn = request_fn or httpx.request
        self.circuit_breaker = circuit_breaker or CircuitBreaker(clock=clock)
        self.contract_version = contract_version
        self.metrics = metrics or MetricsRecorder()

    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None, contract_version: str | None = None, idempotency_key: str | None = None, expected_snapshot_id: str | None = None) -> dict[str, Any]:
        payload = payload or None
        # Trace identity must come only from explicitly controlled trace
        # arguments. Request payloads are business data and must never become
        # audit headers, even when they contain similarly named fields.
        trace = trace or TraceContext.from_mapping(trace_context or {})
        headers = trace.headers()
        expected_contract = contract_version or self.contract_version
        if expected_contract:
            headers["X-Contract-Version"] = expected_contract
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        if not idempotency_key and payload and payload.get("idempotency_key"):
            headers["Idempotency-Key"] = str(payload["idempotency_key"])
        timeout = httpx.Timeout(connect=min(self.timeout_seconds, 5.0), read=self.timeout_seconds, write=self.timeout_seconds, pool=5.0)
        last: Exception | None = None
        try:
            self.circuit_breaker.before_call()
        except CircuitOpenError as exc:
            raise DependencyError(DEPENDENCY_UNAVAILABLE, str(exc), dependency=self.base_url, trace_id=trace.trace_id) from exc
        for attempt in range(self.retry_policy.max_attempts):
            try:
                response = self.request_fn(method, f"{self.base_url}{path}", json=payload, params={key: value for key, value in (params or {}).items() if value is not None}, headers=headers, timeout=timeout)
                if response.status_code in {408, 429, 500, 502, 503, 504}:
                    raise RetryableError(f"dependency returned HTTP {response.status_code}", status_code=response.status_code, retry_after=parse_retry_after(response.headers.get("Retry-After")))
                if response.status_code >= 400:
                    self.circuit_breaker.failure()
                    raise DependencyError(DEPENDENCY_UNAVAILABLE, f"dependency returned HTTP {response.status_code}", dependency=self.base_url, status_code=response.status_code, trace_id=trace.trace_id)
                body = response.json()
                if not isinstance(body, dict):
                    body = {"items": body}
                self._validate_contract(body, expected_contract, expected_snapshot_id, trace.trace_id)
                self.circuit_breaker.success()
                self.metrics.increment("dependency_requests_total", dependency=self.base_url)
                return body
            except DependencyError:
                self.metrics.increment("dependency_errors_total", dependency=self.base_url)
                raise
            except (httpx.TimeoutException, TimeoutError) as exc:
                last = exc
                if attempt + 1 >= self.retry_policy.max_attempts:
                    self.circuit_breaker.failure()
                    self.metrics.increment("dependency_errors_total", dependency=self.base_url, code=DEPENDENCY_TIMEOUT)
                    raise DependencyError(DEPENDENCY_TIMEOUT, "dependency request timed out", dependency=self.base_url, trace_id=trace.trace_id) from exc
                self.sleeper(self.retry_policy.delay(attempt))
            except (httpx.NetworkError, RetryableError) as exc:
                last = exc
                if attempt + 1 >= self.retry_policy.max_attempts:
                    self.circuit_breaker.failure()
                    self.metrics.increment("dependency_errors_total", dependency=self.base_url, code=DEPENDENCY_UNAVAILABLE)
                    raise DependencyError(DEPENDENCY_UNAVAILABLE, "dependency request failed", dependency=self.base_url, status_code=getattr(exc, "status_code", None), trace_id=trace.trace_id) from exc
                self.sleeper(self.retry_policy.delay(attempt, getattr(exc, "retry_after", None)))
            except ValueError as exc:
                self.circuit_breaker.failure()
                raise DependencyError(CONTRACT_MISMATCH, "dependency returned invalid JSON", dependency=self.base_url, trace_id=trace.trace_id) from exc
            except httpx.HTTPError as exc:
                self.circuit_breaker.failure()
                raise DependencyError(DEPENDENCY_UNAVAILABLE, "dependency HTTP error", dependency=self.base_url, trace_id=trace.trace_id) from exc
        raise DependencyError(DEPENDENCY_UNAVAILABLE, "dependency request failed", dependency=self.base_url, trace_id=trace.trace_id) from last

    @staticmethod
    def _validate_contract(body: dict[str, Any], expected_contract: str | None, expected_snapshot_id: str | None, trace_id: str) -> None:
        if body.get("status") in {"STALE", "stale"} or body.get("stale") is True:
            raise DependencyError(STALE_DATA, "dependency returned stale data", trace_id=trace_id)
        if expected_contract and body.get("contract_version") != expected_contract:
            raise DependencyError(CONTRACT_MISMATCH, "dependency contract version mismatch", trace_id=trace_id)
        actual_snapshot = body.get("snapshot_id")
        if actual_snapshot is None and isinstance(body.get("snapshot"), dict):
            actual_snapshot = body["snapshot"].get("snapshot_id")
        if actual_snapshot is None and isinstance(body.get("data"), dict):
            actual_snapshot = body["data"].get("snapshot_id")
        if expected_snapshot_id and actual_snapshot != expected_snapshot_id:
            raise DependencyError(INVALID_SNAPSHOT, "dependency snapshot id mismatch", trace_id=trace_id)

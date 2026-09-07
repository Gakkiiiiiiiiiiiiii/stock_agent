from __future__ import annotations

import hmac
import os
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.model_gateway.metrics import render_global_metrics
from app.observability.metrics import metrics as observability_metrics

PUBLIC_PATHS = {
    "/health/live",
    "/health/ready",
    "/health/version",
    "/metrics",
    "/health/formal-decision-ready",
    "/health/analysis-ready",
    "/health/knowledge-conclusion-ready",
}

# Inventory-controlled deprecation metadata.  Other v1 routes are still
# active read/admin compatibility APIs and must not receive a false sunset.
DEPRECATED_ENDPOINTS = {
    "/api/v1/analyze/stock": "/api/v2/analysis/stock",
    "/api/v1/analyze/theme": "/api/v2/analysis/theme",
    "/api/v1/compatibility/analysis/stock/{symbol}": "/api/v2/analysis/stock",
    "/api/v1/decisions": "/api/v2/decisions",
    "/api/v1/review/trade": "/api/v2/decisions/{decision_id}/review",
}

METRICS = Counter()
_TRACE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


async def security_and_trace_middleware(request: Request, call_next: Callable):
    supplied_trace = request.headers.get("x-trace-id", "")
    trace_id = supplied_trace if _TRACE_ID.fullmatch(supplied_trace) else str(uuid.uuid4())
    request.state.trace_id = trace_id
    started = time.perf_counter()
    if os.getenv("STOCK_AGENT_PROFILE", "full").strip().lower() == "knowledge-only":
        if request.method == "POST" and request.url.path.startswith("/api/v2/knowledge-conclusions") and "application/json" not in request.headers.get("content-type", "").lower():
            return _flat_error(422, "CONTENT_TYPE_INVALID", "Content-Type must be application/json", trace_id, False)
        denied = _knowledge_auth_denied(request, trace_id)
        if denied is not None:
            return denied
    required_key = os.getenv("API_KEY")
    if required_key and request.url.path not in PUBLIC_PATHS:
        auth = request.headers.get("authorization") or ""
        bearer = auth.removeprefix("Bearer ").strip() if auth.lower().startswith("bearer ") else None
        supplied = request.headers.get("x-api-key") or bearer
        if supplied != required_key:
            METRICS["api_auth_denied_total"] += 1
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "valid API key is required",
                        "details": {},
                        "trace_id": trace_id,
                    }
                },
                headers={"x-trace-id": trace_id},
            )
    try:
        response: Response = await call_next(request)
    except Exception:  # noqa: BLE001 - the ASGI boundary must redact every unhandled route error.
        METRICS["api_errors_total"] += 1
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "internal server error",
                    "details": {},
                    "trace_id": trace_id,
                }
            },
            headers={"x-trace-id": trace_id},
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    METRICS["api_requests_total"] += 1
    METRICS[f"api_status_{response.status_code}_total"] += 1
    response.headers["x-trace-id"] = trace_id
    response.headers["x-elapsed-ms"] = str(elapsed_ms)
    route = request.scope.get("route")
    endpoint = getattr(route, "path", None) or request.url.path
    replacement = DEPRECATED_ENDPOINTS.get(endpoint)
    if replacement is not None:
        observability_metrics.inc("legacy_endpoint_requests_total", endpoint=str(endpoint))
        response.headers.setdefault("Deprecation", "true")
        response.headers.setdefault("Sunset", "Wed, 31 Dec 2026 23:59:59 GMT")
        response.headers.setdefault("Link", f"<{replacement}>; rel=\"successor-version\"")
    # §32：统一 Trace Headers，全链路保持同一 trace_id 并透传 decision/caller。
    if request.headers.get("x-decision-id"):
        response.headers["x-decision-id"] = request.headers["x-decision-id"]
    if request.headers.get("x-caller-service"):
        response.headers["x-caller-service"] = request.headers["x-caller-service"]
    return response


def _knowledge_auth_denied(request: Request, trace_id: str) -> JSONResponse | None:
    """Explicit deployment policy for the isolated knowledge-only surface."""
    if request.url.path in PUBLIC_PATHS:
        return None
    mode = os.getenv("STOCK_AGENT_API_AUTH_MODE", "none").strip().lower()
    if mode == "none":
        return None
    if mode != "bearer":
        return _flat_error(503, "AUTH_POLICY_INVALID", "knowledge API authentication policy is unavailable", trace_id, False)
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not auth[7:].strip() or auth[7:].strip() != auth[7:]:
        return _flat_error(401, "UNAUTHORIZED", "valid bearer authorization is required", trace_id, False)
    supplied = auth[7:]
    refs = (os.getenv("STOCK_AGENT_API_AUTH_TOKEN_FILE", ""), os.getenv("STOCK_AGENT_API_AUTH_PREVIOUS_TOKEN_FILE", ""))
    tokens = tuple(token for ref in refs if (token := _read_token_file(ref)))
    if not tokens or not any(hmac.compare_digest(supplied, token) for token in tokens):
        return _flat_error(401, "UNAUTHORIZED", "valid bearer authorization is required", trace_id, False)
    return None


def _read_token_file(reference: str) -> str | None:
    if not reference:
        return None
    try:
        token = Path(reference).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token if token and "\n" not in token and "\r" not in token else None


def _flat_error(status: int, code: str, message: str, trace_id: str, retryable: bool) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message, "trace_id": trace_id, "retryable": retryable}, headers={"x-trace-id": trace_id})


def render_metrics() -> str:
    parts = ["\n".join(f"{key} {value}" for key, value in sorted(METRICS.items()))]
    model_metrics = render_global_metrics()
    if model_metrics:
        parts.append(model_metrics)
    parts.append(observability_metrics.render())
    return "\n".join(part for part in parts if part) + "\n"

from __future__ import annotations

import os
import time
import uuid
from collections import Counter
from collections.abc import Callable

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
}

# Inventory-controlled deprecation metadata.  Other v1 routes are still
# active read/admin compatibility APIs and must not receive a false sunset.
DEPRECATED_ENDPOINTS = {
    "/api/v1/analyze/stock": "/api/v2/analysis/stock",
    "/api/v1/analyze/theme": "/api/v2/analysis/theme",
    "/api/v1/decisions": "/api/v2/decisions",
    "/api/v1/review/trade": "/api/v2/decisions/{decision_id}/review",
}

METRICS = Counter()


async def security_and_trace_middleware(request: Request, call_next: Callable):
    trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
    request.state.trace_id = trace_id
    started = time.perf_counter()
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
    except Exception:  # noqa: BLE001
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


def render_metrics() -> str:
    parts = ["\n".join(f"{key} {value}" for key, value in sorted(METRICS.items()))]
    model_metrics = render_global_metrics()
    if model_metrics:
        parts.append(model_metrics)
    parts.append(observability_metrics.render())
    return "\n".join(part for part in parts if part) + "\n"

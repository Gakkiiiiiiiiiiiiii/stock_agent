from __future__ import annotations

import os
import time
import uuid
from collections import Counter
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response

PUBLIC_PATHS = {
    "/health/live",
    "/health/ready",
    "/metrics",
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
    except Exception as exc:  # noqa: BLE001
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
    return response


def render_metrics() -> str:
    return "\n".join(f"{key} {value}" for key, value in sorted(METRICS.items())) + "\n"

"""Read-only compatibility surface with explicit deprecation semantics."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import dependencies
from app.domain.decision.authority import analysis_response

router = APIRouter(prefix="/api/v1/compatibility", tags=["compatibility"])
SUNSET = "Wed, 31 Dec 2026 23:59:59 GMT"


def _headers() -> dict[str, str]:
    return {"Deprecation": "true", "Sunset": SUNSET, "Link": "</api/v2/analysis>; rel=\"successor-version\""}


@router.get("/analysis/stock/{symbol}")
def compatibility_stock(symbol: str, request: Request) -> dict:
    request.app.state.legacy_endpoint_requests = getattr(request.app.state, "legacy_endpoint_requests", 0) + 1
    # FastAPI route functions cannot directly set response headers without a
    # response argument; the middleware adds these headers for all compatibility routes.
    return analysis_response(dependencies.orchestrator.analyze_stock(symbol), compatibility=True)

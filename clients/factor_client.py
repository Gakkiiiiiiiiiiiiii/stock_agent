from __future__ import annotations

from typing import Any, Protocol

from clients._http import SubsystemHttpClient
from app.model_gateway.metrics import TraceContext
from contracts.factor import AlphaScoreRequest


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    if isinstance(value, dict):
        merged = dict(value)
        for key in ("contract_version", "service_version", "snapshot_id", "as_of", "available_at"):
            if key in payload:
                merged.setdefault(key, payload[key])
        return merged
    return payload


class FactorClient(Protocol):
    def list_factors(self, *, limit: int = 20) -> dict[str, Any]: ...
    def score_alpha(self, request: AlphaScoreRequest, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]: ...


class RemoteFactorClient(SubsystemHttpClient):
    def __init__(self, base_url: str | None = None, *, timeout_seconds: float = 30.0, retries: int = 2, **http_kwargs) -> None:
        import os
        super().__init__(base_url or os.getenv("FACTOR_SERVICE_URL", "http://stock-factor:8200"), timeout_seconds=timeout_seconds, retries=retries, **http_kwargs)

    def list_factors(self, *, limit: int = 20, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.request("GET", "/api/v1/factors", params={"limit": limit}, trace=trace, trace_context=trace_context, contract_version="factor.v1")
        return {"items": payload.get("items", []), "limit": payload.get("limit", limit)}

    def get_factor(self, factor_id: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/factors/{factor_id}", trace=trace, trace_context=trace_context, contract_version="factor.v1"))

    def score_alpha(self, request: AlphaScoreRequest, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _data(self.request("POST", "/api/v1/alpha/score", payload=request.model_dump(exclude_none=True), trace=trace, trace_context=trace_context, contract_version="factor.v1"))

    def get_factor_evidence(self, factor_id: str, *, trace: TraceContext | None = None, trace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only evidence metadata for a factor owned by stock_factor."""
        return _data(self.request("GET", f"/api/v1/factors/{factor_id}/evidence", trace=trace, trace_context=trace_context, contract_version="factor.v1"))

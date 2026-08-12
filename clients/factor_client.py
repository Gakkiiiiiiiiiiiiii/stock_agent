from __future__ import annotations

from typing import Any, Protocol

from clients._http import SubsystemHttpClient
from contracts.factor import AlphaScoreRequest, MiningJobRequest


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else payload


class FactorClient(Protocol):
    def create_mining_job(self, request: MiningJobRequest) -> dict[str, Any]: ...
    def get_mining_job(self, job_id: str) -> dict[str, Any]: ...
    def list_factors(self, *, limit: int = 20) -> dict[str, Any]: ...
    def score_alpha(self, request: AlphaScoreRequest) -> dict[str, Any]: ...
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def cancel_mining_job(self, job_id: str) -> dict[str, Any]: ...


class RemoteFactorClient(SubsystemHttpClient):
    def create_mining_job(self, request: MiningJobRequest | None = None, **kwargs: Any) -> dict[str, Any]:
        request = request or MiningJobRequest(**kwargs)
        return _data(self.request("POST", "/api/v1/mining/jobs", payload=request.model_dump(exclude_none=True)))

    def get_mining_job(self, job_id: str) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/mining/jobs/{job_id}"))

    def list_factors(self, *, limit: int = 20) -> dict[str, Any]:
        payload = self.request("GET", "/api/v1/factors", params={"limit": limit})
        return {"items": payload.get("items", []), "limit": payload.get("limit", limit)}

    def get_factor(self, factor_id: str) -> dict[str, Any]:
        return _data(self.request("GET", f"/api/v1/factors/{factor_id}"))

    def score_alpha(self, request: AlphaScoreRequest) -> dict[str, Any]:
        return _data(self.request("POST", "/api/v1/alpha/score", payload=request.model_dump(exclude_none=True)))

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "universe" in payload and "symbols" not in payload:
            payload = {**payload, "symbols": payload.pop("universe")}
        return _data(self.request("POST", "/api/v1/factors/evaluate", payload=payload))

    def cancel_mining_job(self, job_id: str) -> dict[str, Any]:
        return _data(self.request("POST", f"/api/v1/mining/jobs/{job_id}/cancel"))

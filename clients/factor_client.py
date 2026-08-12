"""Factor service ports plus local and HTTP implementations."""
from __future__ import annotations

from typing import Any, Protocol

from clients._http import SubsystemHttpClient
from contracts.factor import AlphaScoreRequest, MiningJobRequest


class FactorClient(Protocol):
    def create_mining_job(self, request: MiningJobRequest) -> dict[str, Any]: ...
    def get_mining_job(self, job_id: str) -> dict[str, Any]: ...
    def list_factors(self, *, limit: int = 20) -> dict[str, Any]: ...
    def score_alpha(self, request: AlphaScoreRequest) -> dict[str, Any]: ...
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def cancel_mining_job(self, job_id: str) -> dict[str, Any]: ...


class LocalFactorClient:
    """Temporary adapter that keeps the legacy factor engine available for rollback."""

    def __init__(self, server: Any) -> None:
        self._server = server

    def create_mining_job(self, request: MiningJobRequest) -> dict[str, Any]:
        return self._server.mine_factors(**request.model_dump(exclude_none=True))

    def get_mining_job(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "status": "UNSUPPORTED_LOCAL"}

    def list_factors(self, *, limit: int = 20) -> dict[str, Any]:
        return self._server.list_factor_library(limit=limit)

    def score_alpha(self, request: AlphaScoreRequest) -> dict[str, Any]:
        return self._server.scan_alpha_factors(symbols=request.symbols or None)

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._server.evaluate_factor(**payload)

    def cancel_mining_job(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "cancelled": False, "warning": "legacy factor jobs are managed by stock_agent"}


class RemoteFactorClient(SubsystemHttpClient):
    def create_mining_job(self, request: MiningJobRequest) -> dict[str, Any]:
        return self.request("POST", "/api/v1/mining/jobs", payload=request.model_dump())

    def get_mining_job(self, job_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/mining/jobs/{job_id}")

    def list_factors(self, *, limit: int = 20) -> dict[str, Any]:
        return self.request("GET", "/api/v1/factors", params={"limit": limit})

    def score_alpha(self, request: AlphaScoreRequest) -> dict[str, Any]:
        return self.request("POST", "/api/v1/alpha/score", payload=request.model_dump())

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/v1/factors/evaluate", payload=payload)

    def cancel_mining_job(self, job_id: str) -> dict[str, Any]:
        return self.request("POST", f"/api/v1/mining/jobs/{job_id}/cancel")

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from contracts.factor import MiningJobRequest
from services.subsystems import get_factor_client

router = APIRouter()


class FactorMineRequest(BaseModel):
    rounds: int | None = None
    candidates_per_round: int | None = None
    universe: list[str] | None = None
    days: int | None = None
    eval_window: int | None = None


@router.post("/api/v2/factors/mine")
def submit_factor_mine_job(request: FactorMineRequest) -> dict:
    payload = MiningJobRequest(
        rounds=request.rounds,
        candidates_per_round=request.candidates_per_round,
        symbols=request.universe or [],
        days=request.days,
        eval_window=request.eval_window,
    )
    return get_factor_client().create_mining_job(payload)


@router.get("/api/v2/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        return get_factor_client().get_mining_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"factor service unavailable: {exc}") from exc


@router.post("/api/v2/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    return get_factor_client().cancel_mining_job(job_id)

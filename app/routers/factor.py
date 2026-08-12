"""因子任务与通用 job 查询路由（从 app/api.py 平移，路由契约不变）。

/api/v2/jobs/* 目前服务于因子挖掘等 job_task 任务，暂与本路由同组。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from clients.factor_client import RemoteFactorClient
from contracts.factor import MiningJobRequest
from app.routers._shared import _parse_job_result
from services.subsystems import factor_backend
from storage.repositories.job_repository import JobTaskRepository
from workers.job_types import JobType

router = APIRouter()


class FactorMineRequest(BaseModel):
    rounds: int | None = None
    candidates_per_round: int | None = None
    universe: list[str] | None = None
    days: int | None = None
    eval_window: int | None = None


@router.post("/api/v2/factors/mine")
def submit_factor_mine_job(request: FactorMineRequest) -> dict:
    if factor_backend() == "remote":
        payload = MiningJobRequest(
            rounds=request.rounds,
            candidates_per_round=request.candidates_per_round,
            symbols=request.universe or [],
            days=request.days,
            eval_window=request.eval_window,
        )
        return RemoteFactorClient(os.getenv("FACTOR_SERVICE_URL", "http://stock-factor:8200")).create_mining_job(payload)
    task = JobTaskRepository().create(JobType.FACTOR_MINE, request.model_dump(exclude_none=True))
    return {"job_id": task["id"], "status": task["status"]}


@router.get("/api/v2/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    if factor_backend() == "remote":
        return RemoteFactorClient(os.getenv("FACTOR_SERVICE_URL", "http://stock-factor:8200")).get_mining_job(job_id)
    task = JobTaskRepository().get(job_id)
    if task is None:
        raise HTTPException(status_code=404, detail="job not found")
    return task | {"result": _parse_job_result(task.get("result_ref"))}


@router.post("/api/v2/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    if factor_backend() == "remote":
        return RemoteFactorClient(os.getenv("FACTOR_SERVICE_URL", "http://stock-factor:8200")).cancel_mining_job(job_id)
    return {"job_id": job_id, "cancelled": JobTaskRepository().cancel(job_id)}

"""因子任务与通用 job 查询路由（从 app/api.py 平移，路由契约不变）。

/api/v2/jobs/* 目前服务于因子挖掘等 job_task 任务，暂与本路由同组。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers._shared import _parse_job_result
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
    task = JobTaskRepository().create(JobType.FACTOR_MINE, request.model_dump(exclude_none=True))
    return {"job_id": task["id"], "status": task["status"]}


@router.get("/api/v2/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    task = JobTaskRepository().get(job_id)
    if task is None:
        raise HTTPException(status_code=404, detail="job not found")
    return task | {"result": _parse_job_result(task.get("result_ref"))}


@router.post("/api/v2/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    return {"job_id": job_id, "cancelled": JobTaskRepository().cancel(job_id)}

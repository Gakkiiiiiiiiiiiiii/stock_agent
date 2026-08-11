"""市场扫描路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from datetime import date as Date

from fastapi import APIRouter
from pydantic import BaseModel

from app import dependencies

router = APIRouter()


class DailyScanRequest(BaseModel):
    date: Date | None = None
    mode: str = "after_close"


@router.post("/api/v1/market/daily-scan")
def daily_scan(request: DailyScanRequest) -> dict:
    return dependencies.orchestrator.daily_scan(scan_date=request.date, mode=request.mode)

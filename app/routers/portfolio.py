"""组合风险路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from fastapi import APIRouter

from engines.risk.portfolio_risk import evaluate_portfolio_risk
from financial_agent.models import Position

router = APIRouter()


@router.post("/api/v1/risk/portfolio")
def portfolio_risk(positions: list[Position]) -> dict:
    return evaluate_portfolio_risk(positions).model_dump()

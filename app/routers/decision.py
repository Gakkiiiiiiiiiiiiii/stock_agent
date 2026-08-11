"""交易复盘（decision review）路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engines.decision.replay import DecisionReplayService
from financial_agent.models import TradeReviewInput

router = APIRouter()


class DecisionReplayRequest(BaseModel):
    """决策回放请求体：original=按记录版本验证确定性，current=用当前算法对比。"""

    mode: Literal["original", "current"] = "original"


@router.post("/api/v1/review/trade")
def review_trade(request: TradeReviewInput) -> dict:
    return {"status": "accepted", "review": request.model_dump(), "note": "MVP 版本返回结构化复盘输入，数据库写入由后续迁移接入。"}


@router.post("/api/v1/decision/{decision_id}/replay")
def replay_decision(decision_id: str, request: DecisionReplayRequest | None = None) -> dict:
    """决策回放（§27）：重放确定性决策链并与落库产物比对，决策不存在返回 404。"""
    mode = request.mode if request is not None else "original"
    result = DecisionReplayService().replay(decision_id, mode=mode)
    if result.get("error") == "DECISION_NOT_FOUND":
        raise HTTPException(status_code=404, detail="decision not found")
    return result

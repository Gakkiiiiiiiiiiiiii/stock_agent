"""交易复盘（decision review）与决策落库路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engines.decision.decision_service import DecisionService
from engines.decision.replay import DecisionReplayService
from financial_agent.models import TradeReviewInput

router = APIRouter()


class DecisionReplayRequest(BaseModel):
    """决策回放请求体（§27 + 详细修改方案 §6）。

    original/EXACT_REPLAY：固定落库输入验证确定性；current：当前算法对比；
    COUNTERFACTUAL_REPLAY：相同输入 + override（policy_version/model/strategy）反事实分析。
    """

    mode: Literal["original", "current", "multi_agent", "EXACT_REPLAY", "COUNTERFACTUAL_REPLAY"] = "original"
    override: dict | None = None


class DecisionCreateRequest(BaseModel):
    """决策落库请求体（收尾文档 §38/§39）：结构化决策 + DecisionSnapshot 版本锚点。"""

    query: str | None = None
    candidates: list[dict[str, Any]] = []
    market_regime: str | None = None
    themes: list[str] = []
    sector: str | None = None
    market_features: dict[str, Any] = {}
    decision_snapshot: dict[str, Any] = {}
    decision_quality: str | None = None


@router.post("/api/v1/decisions")
def create_decision(request: DecisionCreateRequest) -> dict:
    """决策落库（§38）：持久化决策与 DecisionSnapshot，返回 decision_snapshot_id 供审计/Replay。"""
    payload = request.model_dump(exclude_none=True)
    payload.setdefault("query", "four-repo-e2e decision")
    return DecisionService().save_decision(**payload)


@router.post("/api/v1/review/trade")
def review_trade(request: TradeReviewInput) -> dict:
    return {"status": "accepted", "review": request.model_dump(), "note": "MVP 版本返回结构化复盘输入，数据库写入由后续迁移接入。"}


@router.post("/api/v1/decision/{decision_id}/replay")
@router.post("/api/v1/decisions/{decision_id}/replay")  # §27 规范路径别名
def replay_decision(decision_id: str, request: DecisionReplayRequest | None = None) -> dict:
    """决策回放（§27）：重放确定性决策链并与落库产物比对，决策不存在返回 404。"""
    mode = request.mode if request is not None else "original"
    overrides = request.override if request is not None else None
    result = DecisionReplayService().replay(decision_id, mode=mode, overrides=overrides)
    if result.get("error") == "DECISION_NOT_FOUND":
        raise HTTPException(status_code=404, detail="decision not found")
    return result

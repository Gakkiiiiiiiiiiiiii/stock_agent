"""交易复盘（decision review）与决策落库路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from engines.decision.replay import DecisionReplayService
from contracts.replay import ReplayRequest
from storage.repositories.research_repository import DecisionSnapshotRepository
from storage.repositories.research_repository import OutcomeRepository, ReviewRepository
from contracts.outcome import DecisionOutcome
from contracts.review import DecisionReview
from app.model_gateway.metrics import global_metrics
from financial_agent.models import TradeReviewInput

router = APIRouter()


class DecisionReplayRequest(BaseModel):
    """决策回放请求体（§27 + 详细修改方案 §6）。

    original/EXACT_REPLAY：固定落库输入验证确定性；current：当前算法对比；
    COUNTERFACTUAL_REPLAY：相同输入 + override（policy_version/model/strategy）反事实分析。
    """

    mode: Literal["original", "current", "multi_agent", "EXACT_REPLAY", "COUNTERFACTUAL_REPLAY"] = "original"
    override: dict | None = None


class DecisionReplayV2Request(BaseModel):
    """Strict v2 replay input; never falls back to the legacy adapter."""

    mode: Literal[
        "EXACT_REPLAY", "MODEL_REPLAY", "SKILL_REPLAY", "POLICY_REPLAY",
        "WORKFLOW_REPLAY", "COUNTERFACTUAL_REPLAY",
    ]
    overrides: dict[str, Any] = Field(default_factory=dict)
    snapshot_id: str | None = None


class DecisionCreateRequest(BaseModel):
    """决策落库请求体（收尾文档 §38/§39）：结构化决策 + DecisionSnapshot 版本锚点。"""

    query: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    market_regime: str | None = None
    themes: list[str] = Field(default_factory=list)
    sector: str | None = None
    market_features: dict[str, Any] = Field(default_factory=dict)
    decision_snapshot: dict[str, Any] = Field(default_factory=dict)
    decision_quality: str | None = None


class DecisionV2Request(BaseModel):
    task_type: str
    objective: str
    subjects: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return value


class OutcomeRefreshRequest(BaseModel):
    horizon: str
    measured_at: datetime

    @field_validator("measured_at")
    @classmethod
    def aware_measured_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measured_at must be timezone-aware")
        return value


class ReviewRefreshRequest(BaseModel):
    outcome_refs: list[str] = Field(min_length=1)


@router.post("/api/v1/decisions")
def create_decision(request: DecisionCreateRequest) -> dict:
    """Retained route marker; formal/actionable persistence is v2 bundle-first."""
    raise HTTPException(status_code=410, detail="legacy decision creation is retired; use POST /api/v2/decisions")


@router.post("/api/v2/decisions")
def create_decision_v2(request: DecisionV2Request) -> dict:
    """Formal bundle-first decision path; persistence is runtime-owned."""
    from app import dependencies

    try:
        runtime = getattr(dependencies.orchestrator, "runtime", dependencies.orchestrator)
        return runtime.decide(
            task_type=request.task_type, objective=request.objective,
            subjects=request.subjects, context=request.context, as_of=request.as_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/review/trade")
def review_trade(request: TradeReviewInput) -> dict:
    raise HTTPException(status_code=410, detail="legacy trade review is retired; use D7 decision review")


@router.get("/api/v1/decisions/{decision_id}/snapshot")
def get_decision_snapshot(decision_id: str) -> dict:
    """P0 X-03：返回完整 DecisionSnapshot v2（schema/runtime/proposal/policy/output/lineage）。"""
    snapshot = DecisionSnapshotRepository().get_for_decision(decision_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="decision snapshot not found")
    return {
        "snapshot_id": snapshot.snapshot_id,
        "decision_id": snapshot.decision_id,
        "schema_version": snapshot.schema_version,
        "market": dict(snapshot.market or {}),
        "content": dict(snapshot.content or {}),
        "factor": dict(snapshot.factor or {}),
        "strategy": dict(snapshot.strategy or {}),
        "agent": dict(snapshot.agent or {}),
        "model": dict(snapshot.model or {}),
        "runtime": dict(snapshot.runtime or {}),
        "tools": dict(snapshot.tools or {}),
        "inputs": dict(snapshot.inputs or {}),
        "proposal": dict(snapshot.proposal or {}),
        "policy": dict(snapshot.policy or {}),
        "output": dict(snapshot.output or {}),
        "portfolio": dict(snapshot.portfolio or {}),
        "risk": dict(snapshot.risk or {}),
        "lineage": list(snapshot.lineage or []),
        "decision_quality": snapshot.decision_quality,
    }


@router.get("/api/v2/decisions/{decision_id}/snapshot")
def get_decision_snapshot_v2(decision_id: str) -> dict:
    snapshot = DecisionSnapshotRepository().get_v3_for_decision(decision_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="decision snapshot not found")
    return snapshot.model_dump(mode="json")


@router.post("/api/v2/decisions/{decision_id}/outcomes/refresh")
def refresh_decision_outcome_v2(decision_id: str, request: OutcomeRefreshRequest) -> dict:
    """Fetch/validate an outcome through an injected read-only quant provider."""
    from app import dependencies

    runtime = getattr(dependencies.orchestrator, "runtime", dependencies.orchestrator)
    provider = getattr(runtime, "outcome_provider", None)
    if provider is None:
        from engines.decision.outcome_service import OutcomeService
        provider = OutcomeService()
    try:
        fetch = getattr(provider, "fetch_outcome", None) or getattr(provider, "refresh", None) or getattr(provider, "get_outcome", None)
        if fetch is None:
            raise ValueError("quant outcome provider lacks read-only fetch_outcome")
        raw = fetch(decision_id=decision_id, horizon=request.horizon, measured_at=request.measured_at)
        if isinstance(raw, DecisionOutcome):
            return raw.model_dump(mode="json")
        if not isinstance(raw, dict):
            raise ValueError("quant outcome provider returned a non-object")
        outcome = DecisionOutcome.build(**{**raw, "decision_id": decision_id, "horizon": request.horizon, "measured_at": request.measured_at, "source_system": "quant"})
        row = OutcomeRepository().save(outcome)
    except (ConnectionError, TimeoutError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="quant outcome dependency unavailable") from exc
    except ValueError as exc:
        code = str(exc)
        if code in {"DECISION_NOT_FOUND", "DECISION_SNAPSHOT_NOT_FOUND", "OUTCOME_NOT_FOUND"}:
            raise HTTPException(status_code=404, detail=code) from exc
        raise HTTPException(status_code=422, detail=code) from exc
    return outcome.model_dump(mode="json") | {"outcome_id": row.outcome_id}


@router.get("/api/v2/decisions/{decision_id}/outcomes")
def list_decision_outcomes_v2(decision_id: str) -> dict:
    return {"items": [item.model_dump(mode="json") for item in OutcomeRepository().list_for_decision(decision_id)]}


@router.post("/api/v2/decisions/{decision_id}/review")
def save_decision_review_v2(decision_id: str, request: ReviewRefreshRequest) -> dict:
    """Run the injected structured review pipeline over stored outcome refs."""
    from app import dependencies

    runtime = getattr(dependencies.orchestrator, "runtime", dependencies.orchestrator)
    provider = getattr(runtime, "review_provider", None)
    if provider is None:
        from engines.decision.review_service import ReviewService
        provider = ReviewService()
    try:
        build_review = getattr(provider, "save_review", None) or getattr(provider, "build_review", None) or getattr(provider, "review", None)
        if build_review is None:
            raise ValueError("review provider lacks structured build_review")
        raw = build_review(decision_id=decision_id, outcome_refs=list(request.outcome_refs))
        if isinstance(raw, DecisionReview):
            return raw.model_dump(mode="json")
        if not isinstance(raw, dict):
            raise ValueError("review provider returned a non-object")
        review = DecisionReview.build(**{**raw, "decision_id": decision_id, "outcome_refs": list(request.outcome_refs)})
        row = ReviewRepository().save(review)
    except ValueError as exc:
        code = str(exc)
        if code in {"DECISION_NOT_FOUND", "DECISION_SNAPSHOT_NOT_FOUND", "OUTCOME_NOT_FOUND"}:
            raise HTTPException(status_code=404, detail=code) from exc
        raise HTTPException(status_code=422, detail=code) from exc
    return review.model_dump(mode="json") | {"review_id": row.review_id}


@router.get("/api/v2/decisions/{decision_id}/review")
def get_decision_review_v2(decision_id: str) -> dict:
    review = ReviewRepository().get_for_decision(decision_id)
    if review is None:
        raise HTTPException(status_code=404, detail="decision review not found")
    return review.model_dump(mode="json")


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


@router.post("/api/v2/decisions/{decision_id}/replay")
def replay_decision_v2(decision_id: str, request: DecisionReplayV2Request) -> dict:
    """Replay only an immutable v3 snapshot through the v2 engine entrypoint."""
    from app import dependencies

    try:
        replay_service = getattr(dependencies.orchestrator, "replay_service", None)
        if replay_service is None:
            runtime = getattr(dependencies.orchestrator, "runtime", None)
            replay_service = getattr(runtime, "replay_service", None) if runtime is not None else None
        replay_service = replay_service or DecisionReplayService()
        replay_request = ReplayRequest(
            decision_id=decision_id,
            mode=request.mode,
            overrides=request.overrides,
            snapshot_id=request.snapshot_id,
        )
        result = replay_service.replay_v2(replay_request)
        global_metrics().increment(
            "replay_total",
            mode=request.mode,
            status=(result.get("status") if isinstance(result, dict) else None) or ("REJECTED" if isinstance(result, dict) and result.get("error") else "SUCCEEDED"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    error = result.get("error") if isinstance(result, dict) else None
    if error == "REPLAY_SNAPSHOT_REQUIRED":
        raise HTTPException(status_code=404, detail="decision snapshot v3 not found")
    if error == "DECISION_NOT_FOUND":
        raise HTTPException(status_code=404, detail="decision not found")
    return result

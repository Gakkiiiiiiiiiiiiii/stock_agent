from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.tools.definitions import ToolDefinition
from clients.content_client import RemoteContentClient
from clients.factor_client import RemoteFactorClient
from contracts.factor import AlphaScoreRequest
from storage.repositories.research_repository import DecisionRepository, OutcomeRepository


class SaveInvestmentDecisionInput(BaseModel):
    query: str | None = None
    skill_slug: str | None = None
    market_regime: str | None = None
    market_features: dict = Field(default_factory=dict)
    thesis: dict = Field(default_factory=dict)
    themes: list = Field(default_factory=list)
    candidates: list = Field(default_factory=list)
    portfolio_advice: dict = Field(default_factory=dict)
    confidence: float | None = None
    decision_as_of: str | None = None
    evaluation_anchor: str = "NEXT_SESSION_OPEN"
    benchmark_symbol: str | None = None
    decision_type: str | None = None
    style: str | None = None
    sector: str | None = None
    trigger_conditions: list = Field(default_factory=list)
    invalidation_conditions: list = Field(default_factory=list)
    evidence_refs: list = Field(default_factory=list)
    tool_trace: list = Field(default_factory=list)


class GetDecisionInput(BaseModel):
    decision_id: str


class GetDecisionOutcomeInput(GetDecisionInput):
    horizon_days: int | None = None
    horizon: str | None = None


class DecisionHistoryInput(BaseModel):
    skill_slug: str = Field(min_length=1)
    limit: int = Field(default=20, ge=1, le=200)


class FactorScoresInput(BaseModel):
    symbols: list[str] = Field(min_length=1)
    as_of: str | None = None
    factor_set: str = "production"


class SubjectStateInput(BaseModel):
    subject_key: str = Field(min_length=1)
    as_of: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class RecordDecisionOutcomeInput(GetDecisionInput):
    evaluation_date: str
    horizon_days: int
    benchmark_return: float | None = None
    portfolio_return: float | None = None
    absolute_return: float | None = None
    market_return: float | None = None
    market_excess_return: float | None = None
    style_return: float | None = None
    style_excess_return: float | None = None
    sector_return: float | None = None
    sector_excess_return: float | None = None
    theme_basket_return: float | None = None
    theme_excess_return: float | None = None
    max_drawdown: float | None = None
    max_adverse_excursion: float | None = None
    max_favorable_excursion: float | None = None
    benchmark_route: dict | None = None
    trigger_hit: bool | None = None
    invalidation_hit: bool | None = None
    realized_metrics: dict = Field(default_factory=dict)


class ReviewInvestmentDecisionInput(GetDecisionInput):
    review: dict[str, Any]
    outcome_id: int | None = None


def build_decision_tools() -> list[ToolDefinition]:
    factor = RemoteFactorClient()
    content = RemoteContentClient()

    def get_decision(payload: dict[str, Any]) -> dict[str, Any]:
        decision = DecisionRepository().get(payload["decision_id"])
        if decision is None:
            return {"decision": None}
        return {"decision": {key: value for key, value in decision.__dict__.items() if not key.startswith("_")}}

    def get_outcome(payload: dict[str, Any]) -> dict[str, Any]:
        rows = OutcomeRepository().list_for_decision(payload["decision_id"])
        horizon = payload.get("horizon")
        horizon_days = payload.get("horizon_days")
        if horizon:
            rows = [item for item in rows if item.horizon == horizon]
        elif horizon_days is not None:
            rows = [item for item in rows if item.horizon in {f"T+{horizon_days}", str(horizon_days)}]
        return {"items": [item.model_dump(mode="json") for item in rows], "source_system": "quant"}

    def get_history(payload: dict[str, Any]) -> dict[str, Any]:
        rows = DecisionRepository().list_decisions_for_skill(payload["skill_slug"], limit=int(payload.get("limit", 20)))
        return {
            "items": [{key: value for key, value in row.__dict__.items() if not key.startswith("_")} for row in rows],
            "source_system": "stock_agent",
        }

    def get_factor_scores(payload: dict[str, Any]) -> dict[str, Any]:
        request = AlphaScoreRequest(
            symbols=list(payload["symbols"]),
            as_of=payload.get("as_of"),
            factor_set=payload.get("factor_set", "production"),
        )
        return factor.score_alpha(request)

    def get_subject_state(payload: dict[str, Any]) -> dict[str, Any]:
        subject = payload["subject_key"]
        filters = {"subject_key": subject}
        if payload.get("as_of"):
            filters["as_of"] = payload["as_of"]
        return content.search_video_knowledge(
            subject,
            filters=filters,
            limit=int(payload.get("limit", 20)),
            intent="subject_state",
        )

    return [
        ToolDefinition(name="get_decision", description="Read a previously persisted stock_agent decision.", input_model=GetDecisionInput, executor=get_decision, category="decision"),
        ToolDefinition(name="get_decision_outcome", description="Read quant-owned measured outcomes for a decision.", input_model=GetDecisionOutcomeInput, executor=get_outcome, category="decision"),
        ToolDefinition(name="get_decision_history", description="Read stock_agent decision history for one skill; no persistence.", input_model=DecisionHistoryInput, executor=get_history, category="decision"),
        ToolDefinition(name="get_factor_scores", description="Request read-only alpha scores from stock_factor.", input_model=FactorScoresInput, executor=get_factor_scores, category="factor"),
        ToolDefinition(name="get_subject_state", description="Read subject evidence through controlled stock_content search.", input_model=SubjectStateInput, executor=get_subject_state, category="content"),
    ]

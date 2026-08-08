from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.tools.definitions import ToolDefinition
from mcp_servers import decision_server


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
    benchmark_symbol: str = "000001.SH"
    trigger_conditions: list = Field(default_factory=list)
    invalidation_conditions: list = Field(default_factory=list)
    evidence_refs: list = Field(default_factory=list)
    tool_trace: list = Field(default_factory=list)


class GetDecisionInput(BaseModel):
    decision_id: str


class GetDecisionOutcomeInput(GetDecisionInput):
    horizon_days: int | None = None


class RecordDecisionOutcomeInput(GetDecisionInput):
    evaluation_date: str
    horizon_days: int
    benchmark_return: float | None = None
    portfolio_return: float | None = None
    trigger_hit: bool | None = None
    invalidation_hit: bool | None = None
    realized_metrics: dict = Field(default_factory=dict)


class ReviewInvestmentDecisionInput(GetDecisionInput):
    review: dict[str, Any]
    outcome_id: int | None = None


def build_decision_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(name="save_investment_decision", description="Persist an important research decision for later outcome evaluation and review.", input_model=SaveInvestmentDecisionInput, executor=lambda payload: decision_server.save_investment_decision(**payload), category="decision"),
        ToolDefinition(name="get_decision", description="Get a previously persisted investment decision.", input_model=GetDecisionInput, executor=lambda payload: decision_server.get_decision(**payload), category="decision"),
        ToolDefinition(name="get_decision_outcome", description="Get the measured outcome of an investment decision.", input_model=GetDecisionOutcomeInput, executor=lambda payload: decision_server.get_decision_outcome(**payload), category="decision"),
        ToolDefinition(name="record_decision_outcome", description="Record a measured decision outcome for a review horizon.", input_model=RecordDecisionOutcomeInput, executor=lambda payload: decision_server.record_decision_outcome(**payload), category="decision"),
        ToolDefinition(name="review_investment_decision", description="Store a structured post-outcome review and turn its lessons into strategy memory.", input_model=ReviewInvestmentDecisionInput, executor=lambda payload: decision_server.review_investment_decision(**payload), category="decision"),
    ]

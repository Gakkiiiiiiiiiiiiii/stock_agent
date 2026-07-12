from __future__ import annotations

from engines.portfolio.portfolio_construction_engine import construct_portfolio_actions
from engines.risk.portfolio_risk import evaluate_portfolio_risk
from financial_agent.models import Position


def _normalize_position_payload(item: dict) -> dict:
    payload = dict(item)
    if "market_value" not in payload:
        payload["market_value"] = (
            payload.get("weight")
            or payload.get("position_weight")
            or payload.get("target_weight")
            or payload.get("suggested_weight")
            or 0
        )
    return payload


def evaluate_portfolio_risk_tool(positions: list[dict]) -> dict:
    parsed = [Position.model_validate(_normalize_position_payload(item)) for item in positions]
    return evaluate_portfolio_risk(parsed).model_dump()


def construct_portfolio(candidates: list[dict], positions: list[dict], risk_limits: dict) -> dict:
    return construct_portfolio_actions(candidates=candidates, positions=positions, risk_limits=risk_limits)

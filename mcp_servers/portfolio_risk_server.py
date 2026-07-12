from __future__ import annotations

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

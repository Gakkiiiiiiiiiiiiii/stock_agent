from __future__ import annotations

from engines.opportunity.service import OpportunityRankingService
from engines.portfolio.pipeline import run_portfolio_pipeline
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
    """旧版契约（保留）：final_signal_score 排序 + 两个上限，内部走 v2 流水线适配器。"""
    return construct_portfolio_actions(candidates=candidates, positions=positions, risk_limits=risk_limits)


def rank_opportunities(candidates: list[dict], context: dict | None = None) -> dict:
    """机会排序：eligibility → score → rank，输出 ranked/rejected/meta。"""
    return OpportunityRankingService().rank(candidates, context)


def construct_portfolio_v2(
    candidates: list[dict],
    positions: list[dict],
    context: dict | None = None,
    risk_limits: dict | None = None,
) -> dict:
    """组合构建 v2 流水线。

    risk_limits 为可选的向后兼容参数：提供时映射为自定义 regime 预算与个股上限。
    """
    ctx = dict(context or {})
    rules = None
    if risk_limits:
        import copy

        from engines.portfolio.pipeline import load_portfolio_rules

        rules = copy.deepcopy(load_portfolio_rules())
        regime = "custom_risk_limits"
        rules.setdefault("regime_risk_budget", {})[regime] = {
            "max_total_position": float(risk_limits.get("max_total_position", 1.0))
        }
        if risk_limits.get("max_single_stock") is not None:
            rules.setdefault("exposure", {})["max_single_stock"] = float(risk_limits["max_single_stock"])
        ctx.setdefault("regime", regime)
    return run_portfolio_pipeline(candidates=candidates, positions=positions, context=ctx, rules=rules)

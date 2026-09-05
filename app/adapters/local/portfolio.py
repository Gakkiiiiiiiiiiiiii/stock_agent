"""Local portfolio adapter implementing the application port."""
from __future__ import annotations

import copy

from engines.opportunity.service import OpportunityRankingService
from engines.portfolio.pipeline import load_portfolio_rules, run_portfolio_pipeline
from engines.portfolio.portfolio_construction_engine import construct_portfolio_actions


class LocalPortfolioAdapter:
    """Composition-root adapter for deterministic local portfolio logic."""

    def __init__(self, ranking: OpportunityRankingService | None = None) -> None:
        self.ranking = ranking or OpportunityRankingService()

    def rank_opportunities(self, candidates: list[dict], context: dict | None = None) -> dict:
        return self.ranking.rank(candidates, context)

    def construct_portfolio(self, candidates: list[dict], positions: list[dict], risk_limits: dict) -> dict:
        return construct_portfolio_actions(candidates=candidates, positions=positions, risk_limits=risk_limits)

    def construct_portfolio_v2(
        self,
        candidates: list[dict],
        positions: list[dict],
        context: dict | None = None,
        risk_limits: dict | None = None,
    ) -> dict:
        ctx = dict(context or {})
        rules = None
        if risk_limits:
            rules = copy.deepcopy(load_portfolio_rules())
            regime = "custom_risk_limits"
            rules.setdefault("regime_risk_budget", {})[regime] = {
                "max_total_position": float(risk_limits.get("max_total_position", 1.0))
            }
            if risk_limits.get("max_single_stock") is not None:
                rules.setdefault("exposure", {})["max_single_stock"] = float(risk_limits["max_single_stock"])
            ctx.setdefault("regime", regime)
        return run_portfolio_pipeline(candidates=candidates, positions=positions, context=ctx, rules=rules)

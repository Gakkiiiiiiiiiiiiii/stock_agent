"""Deterministic portfolio application orchestration."""
from __future__ import annotations

from app.ports.portfolio import PortfolioPort


class PortfolioApplicationService:
    """Own the portfolio use cases formerly exposed by the MCP server."""

    def __init__(self, portfolio: PortfolioPort) -> None:
        self.portfolio = portfolio

    def rank_opportunities(self, candidates: list[dict], context: dict | None = None) -> dict:
        return self.portfolio.rank_opportunities(candidates=candidates, context=context)

    def construct_portfolio_v2(
        self,
        candidates: list[dict],
        positions: list[dict],
        context: dict | None = None,
        risk_limits: dict | None = None,
    ) -> dict:
        return self.portfolio.construct_portfolio_v2(
            candidates=candidates,
            positions=positions,
            context=context,
            risk_limits=risk_limits,
        )

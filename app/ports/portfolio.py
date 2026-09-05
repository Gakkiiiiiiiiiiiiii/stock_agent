"""Application boundary for deterministic portfolio computation."""
from __future__ import annotations

from typing import Protocol


class PortfolioPort(Protocol):
    def rank_opportunities(self, candidates: list[dict], context: dict | None = None) -> dict: ...

    def construct_portfolio_v2(
        self,
        candidates: list[dict],
        positions: list[dict],
        context: dict | None = None,
        risk_limits: dict | None = None,
    ) -> dict: ...

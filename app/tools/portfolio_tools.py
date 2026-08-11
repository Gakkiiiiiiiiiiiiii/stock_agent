from __future__ import annotations

from pydantic import BaseModel

from app.tools.definitions import ToolDefinition
from mcp_servers import portfolio_server


class RankOpportunitiesInput(BaseModel):
    candidates: list[dict]
    context: dict | None = None


class ConstructPortfolioV2Input(BaseModel):
    candidates: list[dict]
    positions: list[dict]
    context: dict | None = None
    risk_limits: dict | None = None


def build_portfolio_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="rank_opportunities",
            description="Rank opportunity candidates deterministically: eligibility filter, weighted opportunity score, and ranked output with evidence refs.",
            input_model=RankOpportunitiesInput,
            executor=lambda payload: portfolio_server.rank_opportunities(**payload),
            category="portfolio",
        ),
        ToolDefinition(
            name="construct_portfolio_v2",
            description="Construct portfolio actions via the v2 pipeline: eligibility, scoring, regime risk budget, sizing bands, exposure caps, and turnover control with machine-readable reason codes.",
            input_model=ConstructPortfolioV2Input,
            executor=lambda payload: portfolio_server.construct_portfolio_v2(**payload),
            category="portfolio",
        ),
    ]

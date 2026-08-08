from __future__ import annotations

from pydantic import BaseModel

from app.tools.definitions import ToolDefinition
from mcp_servers import market_regime_server


class GetMarketRegimeHistoryInput(BaseModel):
    market_code: str = "CN_A"
    start_date: str | None = None
    end_date: str | None = None
    limit: int = 100


def build_regime_tools() -> list[ToolDefinition]:
    return [ToolDefinition(name="get_market_regime_history", description="Get persisted historical market regime intervals for a review period.", input_model=GetMarketRegimeHistoryInput, executor=lambda payload: market_regime_server.get_market_regime_history(**payload), category="regime")]

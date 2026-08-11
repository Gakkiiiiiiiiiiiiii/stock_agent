from __future__ import annotations

from pydantic import BaseModel

from app.tools.definitions import ToolDefinition
from mcp_servers import market_data_server


class GetKlineInput(BaseModel):
    symbol: str
    start_date: str | None = None
    end_date: str | None = None
    freq: str | None = None
    adjust: str | None = None


class EmptyInput(BaseModel):
    pass


def build_market_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(name="get_kline", description="Get historical K-line data for a symbol.", input_model=GetKlineInput, executor=lambda payload: market_data_server.get_kline(**payload), category="market"),
        ToolDefinition(name="get_market_snapshot", description="Get a structured market snapshot for the current market regime.", input_model=EmptyInput, executor=lambda _payload: market_data_server.get_market_snapshot(), category="market"),
        ToolDefinition(name="get_market_features", description="Get computed market features with data quality metadata (coverage, quality flags, calculation version).", input_model=EmptyInput, executor=lambda _payload: market_data_server.get_market_features(), category="market"),
        ToolDefinition(name="get_sector_strength", description="Get sector or theme strength ranking.", input_model=EmptyInput, executor=lambda _payload: market_data_server.get_sector_strength(), category="market"),
    ]

from __future__ import annotations

from pydantic import BaseModel

from app.tools.definitions import ToolDefinition
from services.subsystems import get_quant_client


class GetMarketRegimeHistoryInput(BaseModel):
    market_code: str = "CN_A"
    start_date: str | None = None
    end_date: str | None = None
    limit: int = 100


class GetMarketRegimeInput(BaseModel):
    as_of: str | None = None


def build_regime_tools() -> list[ToolDefinition]:
    client = get_quant_client()
    return [
        ToolDefinition(name="get_market_regime", description="Read market regime evidence computed by quant.", input_model=GetMarketRegimeInput, executor=lambda payload: client.get_market_regime(as_of=payload.get("as_of")), category="regime"),
        ToolDefinition(name="get_market_regime_history", description="Read historical market regime evidence from quant.", input_model=GetMarketRegimeHistoryInput, executor=lambda payload: client.get_market_regime_history(start=payload.get("start_date"), end=payload.get("end_date"), limit=payload.get("limit", 100)), category="regime"),
    ]

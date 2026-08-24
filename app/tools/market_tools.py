from __future__ import annotations

from pydantic import BaseModel, Field

from app.tools.definitions import ToolDefinition
from services.subsystems import get_quant_client


class EmptyInput(BaseModel):
    pass


class MarketSnapshotInput(BaseModel):
    snapshot_id: str = "latest"


class MarketFeaturesInput(BaseModel):
    symbol: str = "000001.SH"
    start: str = ""
    end: str = ""


class SectorStrengthInput(BaseModel):
    as_of: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class TechnicalEvidenceInput(BaseModel):
    symbol: str
    as_of: str | None = None


class PortfolioInput(BaseModel):
    account_id: str | None = None


def build_market_tools() -> list[ToolDefinition]:
    client = get_quant_client()
    return [
        ToolDefinition(name="get_market_snapshot", description="Read the immutable market snapshot owned by quant.", input_model=MarketSnapshotInput, executor=lambda payload: client.get_market_snapshot(payload.get("snapshot_id", "latest")), category="market"),
        ToolDefinition(name="get_market_features", description="Read market feature evidence and quality metadata from quant.", input_model=MarketFeaturesInput, executor=lambda payload: client.get_market_features(payload.get("symbol", "000001.SH"), payload.get("start", ""), payload.get("end", "")), category="market"),
        ToolDefinition(name="get_sector_strength", description="Read sector strength evidence from quant.", input_model=SectorStrengthInput, executor=lambda payload: client.get_sector_strength(as_of=payload.get("as_of"), limit=int(payload.get("limit", 20))), category="market"),
        ToolDefinition(name="get_technical_evidence", description="Read technical evidence computed by quant; no local indicator production.", input_model=TechnicalEvidenceInput, executor=lambda payload: client.get_technical_evidence(payload["symbol"], as_of=payload.get("as_of")), category="market"),
        ToolDefinition(name="get_portfolio_snapshot", description="Read current portfolio facts from quant.", input_model=PortfolioInput, executor=lambda payload: client.get_portfolio_snapshot(payload.get("account_id")), category="market"),
        ToolDefinition(name="get_portfolio_risk_inputs", description="Read portfolio risk inputs from quant.", input_model=PortfolioInput, executor=lambda payload: client.get_portfolio_risk_inputs(payload.get("account_id")), category="market"),
    ]

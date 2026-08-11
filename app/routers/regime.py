"""市场状态（regime）路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class MarketRegimeRequest(BaseModel):
    snapshot: dict | None = None
    as_of: datetime | None = None
    up_count: int | None = None
    down_count: int | None = None
    index_return_5d: float | None = None
    index_return_20d: float | None = None
    top_theme_strength: float | None = None
    limit_up_count: int | None = None
    index_volatility: float | None = None
    index_volatility_20d: float | None = None
    index_drawdown_20d: float | None = None
    limit_down_count: int | None = None
    previous_regime: str | None = None
    high_position_loss_ratio: float | None = None
    high_position_limit_down_ratio: float | None = None
    high_position_breakdown_ratio: float | None = None
    high_position_big_negative_count: int | None = None
    retreat_days: int | None = None
    force_refresh: bool = False


@router.post("/api/v1/market/regime")
def market_regime(request: MarketRegimeRequest) -> dict:
    from mcp_servers.market_regime_server import get_market_regime

    return get_market_regime(**request.model_dump())

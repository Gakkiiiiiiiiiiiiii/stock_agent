"""市场状态（regime）路由（从 app/api.py 平移，路由契约不变）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

from app import dependencies

router = APIRouter()


class MarketRegimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str | None = None
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def _require_aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("as_of must include an explicit timezone")
        return value


@router.post("/api/v1/market/regime")
def market_regime(request: MarketRegimeRequest) -> dict:
    try:
        return dependencies.quant_client.get_market_regime(as_of=request.as_of.isoformat() if request.as_of else None)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "degraded", "dependency": "quant", "reason_code": "DEPENDENCY_UNAVAILABLE", "detail": type(exc).__name__})

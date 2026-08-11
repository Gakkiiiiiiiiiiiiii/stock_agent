"""Realtime event ingress.  The engine is reusable by Redis consumer workers."""
from __future__ import annotations

from fastapi import APIRouter

from engines.market.streaming import MarketEvent, StreamingFeatureEngine
from engines.market.realtime_state_repository import RealtimeFeatureStateRepository

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])
_engine = StreamingFeatureEngine()
_state = RealtimeFeatureStateRepository()


@router.post("/market-events")
def ingest_market_event(event: MarketEvent) -> dict:
    result = _engine.process(event)
    if result.get("accepted"):
        _state.save_symbol(event.symbol, _engine.symbol_features(event.symbol))
        _state.save_aggregate(_engine.aggregate_features())
    return result


@router.get("/market-features/{symbol}")
def current_market_features(symbol: str) -> dict:
    return _state.get_symbol(symbol) or {}


@router.get("/market-features")
def current_market_aggregate() -> dict:
    return _state.get_aggregate() or {}

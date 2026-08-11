"""Realtime event ingress.  The engine is reusable by Redis consumer workers."""
from __future__ import annotations

from fastapi import APIRouter

from engines.market.streaming import MarketEvent, StreamingFeatureEngine

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])
_engine = StreamingFeatureEngine()


@router.post("/market-events")
def ingest_market_event(event: MarketEvent) -> dict:
    return _engine.process(event)


@router.get("/market-features/{symbol}")
def current_market_features(symbol: str) -> dict:
    return _engine.symbol_features(symbol)

"""Realtime event ingress.  The engine is reusable by Redis consumer workers."""
from __future__ import annotations

import os
from functools import lru_cache

from fastapi import APIRouter, HTTPException

from engines.market.streaming import MarketEvent, StreamingFeatureEngine
from engines.market.realtime_state_repository import RealtimeFeatureStateRepository
from engines.market.stream_repository import RedisMarketEventStream

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])
_engine = StreamingFeatureEngine()


@lru_cache(maxsize=1)
def _state() -> RealtimeFeatureStateRepository:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        # Development/test is explicit; production must configure REDIS_URL so
        # API reads the same state written by the stream worker.
        return RealtimeFeatureStateRepository()
    try:
        import redis
        client = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        return RealtimeFeatureStateRepository(client)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("REALTIME_STATE_REDIS_UNAVAILABLE") from exc


@router.post("/market-events")
def ingest_market_event(event: MarketEvent) -> dict:
    if os.getenv("REDIS_URL"):
        try:
            repository = _state()
            RedisMarketEventStream(repository.client, os.getenv("MARKET_EVENT_STREAM_KEY", "market:events:v1")).append(event)
            return {"accepted": True, "queued": True, "event_id": event.event_id}
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    result = _engine.process(event)
    if result.get("accepted"):
        _state().save_symbol(event.symbol, _engine.symbol_features(event.symbol))
        _state().save_aggregate(_engine.aggregate_features())
    return result


@router.get("/market-features/{symbol}")
def current_market_features(symbol: str) -> dict:
    try:
        return _state().get_symbol(symbol) or {}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/market-features")
def current_market_aggregate() -> dict:
    try:
        return _state().get_aggregate() or {}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

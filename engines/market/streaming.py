"""Incremental market-feature overlay with event-time watermark semantics.

Redis is optional at this boundary: callers may use the deterministic in-memory
event log for replay/tests, while production injects a Redis Streams client.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
from statistics import mean
from typing import Iterable
from uuid import uuid4

from pydantic import BaseModel, Field


class MarketEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    exchange: str
    event_time: datetime
    receive_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last: float = Field(gt=0)
    volume: float = Field(ge=0)
    amount: float = Field(ge=0)
    bid: list[float] = Field(default_factory=list)
    ask: list[float] = Field(default_factory=list)
    source: str = "qmt"
    schema_version: int = 1
    sector: str | None = None
    at_limit_up: bool = False


class InMemoryEventStream:
    def __init__(self) -> None:
        self.events: list[MarketEvent] = []
        self._ids: set[str] = set()

    def append(self, event: MarketEvent) -> bool:
        if event.event_id in self._ids:
            return False
        self._ids.add(event.event_id)
        self.events.append(event)
        return True

    def read(self) -> list[MarketEvent]:
        return list(self.events)


class StreamingFeatureEngine:
    def __init__(self, allowed_lateness_seconds: int = 5) -> None:
        self.allowed_lateness_seconds = allowed_lateness_seconds
        self._max_event_time: datetime | None = None
        self._events: dict[str, deque[MarketEvent]] = defaultdict(lambda: deque(maxlen=2_000))
        self._seen: set[str] = set()
        self.late_events: list[str] = []
        self.version = 0

    @property
    def watermark(self) -> datetime | None:
        if self._max_event_time is None:
            return None
        from datetime import timedelta
        return self._max_event_time - timedelta(seconds=self.allowed_lateness_seconds)

    def process(self, event: MarketEvent) -> dict:
        if event.event_id in self._seen:
            return {"accepted": False, "reason": "DUPLICATE_EVENT", "stream_state_version": self.version}
        watermark = self.watermark
        if watermark is not None and event.event_time < watermark:
            self._seen.add(event.event_id)
            self.late_events.append(event.event_id)
            return {"accepted": False, "reason": "LATE_EVENT", "watermark": watermark.isoformat(), "stream_state_version": self.version}
        self._seen.add(event.event_id)
        if self._max_event_time is None or event.event_time > self._max_event_time:
            self._max_event_time = event.event_time
        self._events[event.symbol].append(event)
        self.version += 1
        return {"accepted": True, "stream_state_version": self.version, "watermark": self.watermark.isoformat() if self.watermark else None}

    def symbol_features(self, symbol: str) -> dict:
        events = list(self._events.get(symbol) or [])
        if not events:
            return {}
        latest = events[-1]
        def _return(seconds: int) -> float | None:
            prior = next((item for item in reversed(events) if (latest.event_time - item.event_time).total_seconds() >= seconds), None)
            return round(latest.last / prior.last - 1, 8) if prior else None
        cumulative_volume = sum(item.volume for item in events)
        cumulative_amount = sum(item.amount for item in events)
        vwap = cumulative_amount / cumulative_volume if cumulative_volume else latest.last
        return {
            "symbol": symbol,
            "return_1m": _return(60),
            "return_5m": _return(300),
            "return_15m": _return(900),
            "intraday_vwap": round(vwap, 6),
            "distance_to_vwap": round(latest.last / vwap - 1, 8) if vwap else None,
            "volume_ratio": round(latest.volume / mean(item.volume for item in events), 6) if events else None,
            "amount_acceleration": self._amount_acceleration(events),
            "high_low_position": self._high_low_position(events),
            "as_of": latest.event_time.isoformat(),
            "stream_state_version": self.version,
        }

    @staticmethod
    def _amount_acceleration(events: list[MarketEvent]) -> float | None:
        if len(events) < 4:
            return None
        midpoint = len(events) // 2
        old, recent = sum(item.amount for item in events[:midpoint]), sum(item.amount for item in events[midpoint:])
        return round(recent / old - 1, 8) if old else None

    @staticmethod
    def _high_low_position(events: list[MarketEvent]) -> float:
        values = [item.last for item in events]
        high, low = max(values), min(values)
        return round((values[-1] - low) / (high - low), 8) if high > low else 0.5

    def current_view(self, base_snapshot: dict, symbol: str) -> dict:
        """Merge an immutable daily snapshot with the current realtime overlay."""
        return {
            **dict(base_snapshot),
            "realtime_overlay": self.symbol_features(symbol),
            "base_snapshot_version": base_snapshot.get("feature_version") or base_snapshot.get("calculation_version"),
            "stream_state_version": self.version,
        }

    def aggregate_features(self) -> dict:
        """Intraday breadth and sector-relative strength from the same symbol
        state, rather than a second independent feature source."""
        features = [self.symbol_features(symbol) for symbol in sorted(self._events)]
        valid = [item for item in features if item.get("return_1m") is not None]
        breadth = sum(item["return_1m"] > 0 for item in valid) / len(valid) if valid else None
        sectors: dict[str, list[float]] = defaultdict(list)
        limit_up: dict[str, int] = defaultdict(int)
        counts: dict[str, int] = defaultdict(int)
        for symbol, events in self._events.items():
            if not events: continue
            sector = events[-1].sector or "UNKNOWN"
            value = self.symbol_features(symbol).get("return_1m")
            if value is not None: sectors[sector].append(value)
            counts[sector] += 1
            limit_up[sector] += int(events[-1].at_limit_up)
        market_return = mean([item["return_1m"] for item in valid]) if valid else None
        return {"breadth_intraday": breadth, "sector_relative_strength_intraday": {sector: round(mean(values) - market_return, 8) if market_return is not None else None for sector, values in sectors.items()}, "limit_up_breadth": {sector: round(limit_up[sector] / counts[sector], 8) for sector in counts}, "stream_state_version": self.version}

    @classmethod
    def replay(cls, events: Iterable[MarketEvent], allowed_lateness_seconds: int = 5) -> "StreamingFeatureEngine":
        engine = cls(allowed_lateness_seconds=allowed_lateness_seconds)
        for event in events:
            engine.process(event)
        return engine

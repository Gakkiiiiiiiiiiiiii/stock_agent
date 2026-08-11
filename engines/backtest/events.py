"""Replayable signal → order → fill event contracts."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(StrEnum):
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"
    REJECT = "REJECT"


class SignalEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    side: str
    signal_time: datetime
    target_weight: float | None = None


class OrderEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    signal_event_id: str
    symbol: str
    side: str
    order_time: datetime
    execution_model: str
    quantity: float
    limit_price: float | None = None


class FillEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    order_event_id: str
    symbol: str
    side: str
    fill_time: datetime
    expected_price: float | None = None
    fill_price: float | None = None
    slippage: float | None = None
    reject_reason: str | None = None

    def assert_after_signal(self, signal: SignalEvent) -> None:
        if self.fill_time <= signal.signal_time:
            raise ValueError("LOOKAHEAD_VIOLATION: fill_time must be after signal_time")

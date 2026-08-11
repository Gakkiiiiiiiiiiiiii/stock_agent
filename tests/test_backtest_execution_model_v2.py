from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engines.backtest.events import SignalEvent
from engines.backtest.execution_model import ExecutionModel, resolve_fill, schedule_order


def _signal() -> SignalEvent:
    return SignalEvent(symbol="600000.SH", side="BUY", signal_time=datetime(2026, 1, 1, 15, tzinfo=UTC))


def test_close_signal_cannot_fill_same_day_open():
    signal = _signal()
    with pytest.raises(ValueError, match="LOOKAHEAD"):
        schedule_order(signal, datetime(2026, 1, 1, 9, 30, tzinfo=UTC), ExecutionModel.NEXT_OPEN, 100)


def test_execution_models_and_timeline_are_replayable():
    signal = _signal()
    next_open = datetime(2026, 1, 2, 9, 30, tzinfo=UTC)
    order = schedule_order(signal, next_open, ExecutionModel.NEXT_OPEN, 100)
    assert resolve_fill(order, signal, {"open": 10}, next_open).fill_price == 10
    close = resolve_fill(schedule_order(signal, next_open, ExecutionModel.NEXT_CLOSE, 100), signal, {"close": 11}, next_open + timedelta(hours=6))
    assert close.fill_price == 11
    vwap = resolve_fill(schedule_order(signal, next_open, ExecutionModel.VWAP, 100), signal, {"amount": 1200, "volume": 100}, next_open)
    assert vwap.fill_price == 12
    limit = resolve_fill(schedule_order(signal, next_open, ExecutionModel.LIMIT_PRICE, 100, limit_price=9), signal, {"low": 10, "high": 11}, next_open)
    assert limit.reject_reason == "LIMIT_PRICE_NOT_REACHED"

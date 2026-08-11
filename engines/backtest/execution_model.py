"""Execution-time semantics independent from A-share tradability rules."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from enum import StrEnum

from engines.backtest.events import FillEvent, OrderEvent, SignalEvent


class ExecutionModel(StrEnum):
    NEXT_OPEN = "NEXT_OPEN"
    NEXT_CLOSE = "NEXT_CLOSE"
    VWAP = "VWAP"
    LIMIT_PRICE = "LIMIT_PRICE"


def schedule_order(signal: SignalEvent, next_session: datetime, model: ExecutionModel | str, quantity: float, limit_price: float | None = None) -> OrderEvent:
    resolved = ExecutionModel(model)
    if next_session <= signal.signal_time:
        raise ValueError("LOOKAHEAD_VIOLATION: order time must be after signal time")
    return OrderEvent(signal_event_id=signal.event_id, symbol=signal.symbol, side=signal.side, order_time=next_session, execution_model=resolved.value, quantity=quantity, limit_price=limit_price)


def resolve_fill(order: OrderEvent, signal: SignalEvent, bar: dict, fill_time: datetime) -> FillEvent:
    model = ExecutionModel(order.execution_model)
    expected = None
    price = None
    reason = None
    if model == ExecutionModel.NEXT_OPEN:
        expected = price = _positive(bar.get("open"))
    elif model == ExecutionModel.NEXT_CLOSE:
        expected = price = _positive(bar.get("close"))
    elif model == ExecutionModel.VWAP:
        volume = _positive(bar.get("volume"))
        amount = _positive(bar.get("amount"))
        expected = price = amount / volume if volume and amount else _positive(bar.get("vwap"))
    else:
        limit = order.limit_price
        low, high = _positive(bar.get("low")), _positive(bar.get("high"))
        if limit is None or low is None or high is None or not low <= limit <= high:
            reason = "LIMIT_PRICE_NOT_REACHED"
        else:
            expected = price = limit
    result = FillEvent(order_event_id=order.event_id, symbol=order.symbol, side=order.side, fill_time=fill_time, expected_price=expected, fill_price=price, slippage=(price - expected if price is not None and expected is not None else None), reject_reason=reason)
    result.assert_after_signal(signal)
    return result


def _positive(value) -> float | None:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None

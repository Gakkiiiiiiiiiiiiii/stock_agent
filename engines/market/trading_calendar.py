from __future__ import annotations

from datetime import UTC, date, datetime

from engines.market.trading_clock import TradingClock, get_default_clock


def next_trading_day(day: date, *, clock: TradingClock | None = None) -> date:
    """Return the next session from the injected/default trading clock."""
    return (clock or get_default_clock()).calendar.next_session(day)


def previous_trading_day(day: date, *, clock: TradingClock | None = None) -> date:
    """Return the previous session from the injected/default trading clock."""
    return (clock or get_default_clock()).calendar.previous_session(day)


def latest_available_trading_day(as_of: date | datetime | None = None, *, clock: TradingClock | None = None) -> date:
    """Return the latest completed session at or before ``as_of``."""
    return (clock or get_default_clock()).calendar.normalize(_as_date(as_of) or datetime.now(UTC).date())


def normalize_trading_date(as_of: date | datetime, *, clock: TradingClock | None = None) -> date:
    """Normalize a runtime timestamp with the injected/default clock calendar."""
    return (clock or get_default_clock()).calendar.normalize(as_of)


def advance_trading_days(day: date, sessions: int, *, clock: TradingClock | None = None) -> date:
    """Advance sessions using the injected/default trading clock calendar."""
    return (clock or get_default_clock()).calendar.advance_sessions(day, sessions)


def _as_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


__all__ = ["advance_trading_days", "latest_available_trading_day", "next_trading_day", "normalize_trading_date", "previous_trading_day"]

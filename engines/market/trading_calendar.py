from __future__ import annotations

from datetime import date, datetime, timedelta

from engines.market.qmt_bridge_client import QmtBridgeClient, QmtBridgeError
from engines.market.exchange_calendar import ExchangeTradingCalendar


def next_trading_day(day: date) -> date:
    """Return the next A-share trading day after ``day``.

    QMT is the source of truth when available. The weekend fallback keeps local
    tests deterministic but is deliberately only a fallback.
    """

    qmt_day = _next_qmt_index_day(day)
    if qmt_day is not None:
        return qmt_day
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def previous_trading_day(day: date) -> date:
    """Return the previous A-share trading day before ``day``."""
    qmt_day = _previous_qmt_index_day(day)
    if qmt_day is not None:
        return qmt_day
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def latest_available_trading_day(as_of: date | datetime | None = None) -> date:
    """Return the latest completed/available trading day at or before ``as_of``."""
    day = _as_date(as_of) or date.today()
    qmt_day = _latest_qmt_index_day(day)
    if qmt_day is not None:
        return qmt_day
    candidate = day
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def normalize_trading_date(as_of: date | datetime) -> date:
    """Normalize a runtime timestamp to its trading-date bucket without remote I/O."""
    return ExchangeTradingCalendar().normalize(as_of)


def advance_trading_days(day: date, sessions: int) -> date:
    """Advance for scheduled work without invoking QMT on every agent response.

    The worker still evaluates using real market data; this lightweight calendar
    only decides when a job becomes eligible and conservatively skips weekends.
    """
    return ExchangeTradingCalendar().advance_sessions(day, sessions)


def _next_qmt_index_day(day: date) -> date | None:
    start = day + timedelta(days=1)
    end = day + timedelta(days=15)
    try:
        rows = QmtBridgeClient().get_history(
            symbols=["000001.SH"],
            period="1d",
            start_time=start.strftime("%Y%m%d"),
            end_time=end.strftime("%Y%m%d"),
            dividend_type="none",
            fill_data=False,
            prefer_cache_first=True,
        )
    except QmtBridgeError:
        return None
    dates = sorted({_parse_trade_date(row) for row in rows if _parse_trade_date(row) is not None})
    return dates[0] if dates else None


def _previous_qmt_index_day(day: date) -> date | None:
    start = day - timedelta(days=30)
    end = day - timedelta(days=1)
    try:
        rows = QmtBridgeClient().get_history(
            symbols=["000001.SH"],
            period="1d",
            start_time=start.strftime("%Y%m%d"),
            end_time=end.strftime("%Y%m%d"),
            dividend_type="none",
            fill_data=False,
            prefer_cache_first=True,
        )
    except QmtBridgeError:
        return None
    dates = sorted({_parse_trade_date(row) for row in rows if _parse_trade_date(row) is not None})
    return dates[-1] if dates else None


def _latest_qmt_index_day(day: date) -> date | None:
    start = day - timedelta(days=30)
    try:
        rows = QmtBridgeClient().get_history(
            symbols=["000001.SH"],
            period="1d",
            start_time=start.strftime("%Y%m%d"),
            end_time=day.strftime("%Y%m%d"),
            dividend_type="none",
            fill_data=False,
            prefer_cache_first=True,
        )
    except QmtBridgeError:
        return None
    dates = sorted({_parse_trade_date(row) for row in rows if _parse_trade_date(row) is not None and _parse_trade_date(row) <= day})
    return dates[-1] if dates else None


def _as_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def _parse_trade_date(row: dict) -> date | None:
    raw = row.get("time") or row.get("date") or row.get("trading_date") or row.get("trade_date")
    if raw is None:
        return None
    text = str(raw)
    if len(text) >= 8 and text[:8].isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


__all__ = ["next_trading_day", "previous_trading_day", "latest_available_trading_day", "normalize_trading_date", "advance_trading_days"]

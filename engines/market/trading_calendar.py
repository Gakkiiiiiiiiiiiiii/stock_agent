from __future__ import annotations

from datetime import date, timedelta

from engines.market.qmt_bridge_client import QmtBridgeClient, QmtBridgeError


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


__all__ = ["next_trading_day"]

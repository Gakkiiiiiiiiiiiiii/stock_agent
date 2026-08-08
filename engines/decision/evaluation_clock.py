from __future__ import annotations

from datetime import date, datetime

from engines.market.market_clock import MarketClock


def outcome_not_before(day: date) -> datetime:
    """Delay outcome evaluation until the daily close is safely available."""
    return MarketClock().after_close(day, hour=16)

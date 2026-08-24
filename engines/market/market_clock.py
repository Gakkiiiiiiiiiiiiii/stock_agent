from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from engines.market.trading_clock import TradingClock


CN_TZ = ZoneInfo("Asia/Shanghai")


class MarketClock(TradingClock):
    """Single time boundary for A-share business dates and post-close work."""

    timezone = CN_TZ

    pass

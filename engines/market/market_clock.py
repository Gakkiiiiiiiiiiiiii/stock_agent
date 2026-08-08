from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from engines.market.exchange_calendar import ExchangeTradingCalendar


CN_TZ = ZoneInfo("Asia/Shanghai")


class MarketClock:
    """Single time boundary for A-share business dates and post-close work."""

    timezone = CN_TZ

    def localize(self, value: datetime) -> datetime:
        return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(self.timezone)

    def calendar_date(self, value: datetime) -> date:
        return self.localize(value).date()

    def trading_session(self, value: datetime) -> date:
        return ExchangeTradingCalendar().normalize(self.calendar_date(value))

    def after_close(self, day: date, hour: int = 16) -> datetime:
        return datetime.combine(day, time(hour, 0), tzinfo=self.timezone).astimezone(UTC)

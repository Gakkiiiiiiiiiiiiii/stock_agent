from __future__ import annotations

from datetime import date

from sqlalchemy import select

from storage.db import session_scope
from storage.models.research import MarketTradingCalendar


class ExchangeCalendarRepository:
    def get(self, market_code: str, day: date) -> MarketTradingCalendar | None:
        with session_scope() as session:
            return session.get(MarketTradingCalendar, {"market_code": market_code, "trading_date": day})

    def upsert_many(self, market_code: str, rows: dict[date, bool], source: str) -> None:
        with session_scope() as session:
            for day, is_open in rows.items():
                record = session.get(MarketTradingCalendar, {"market_code": market_code, "trading_date": day})
                if record is None:
                    record = MarketTradingCalendar(market_code=market_code, trading_date=day, is_open=is_open, source=source)
                else:
                    record.is_open, record.source = is_open, source
                session.add(record)

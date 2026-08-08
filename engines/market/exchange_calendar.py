from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from engines.market.qmt_bridge_client import QmtBridgeClient, QmtBridgeError
from storage.repositories.exchange_calendar_repository import ExchangeCalendarRepository


class ExchangeTradingCalendar:
    """A-share session calendar backed by cached QMT index sessions with an explicit fallback."""

    _remote_unavailable_until: datetime | None = None
    remote_retry_seconds = 600

    def __init__(self, market_code: str = "CN_A", repository: ExchangeCalendarRepository | None = None, bridge=None) -> None:
        self.market_code = market_code
        self.repository = repository or ExchangeCalendarRepository()
        self.bridge = bridge or QmtBridgeClient()

    def is_trading_day(self, day: date) -> bool:
        record = self.repository.get(self.market_code, day)
        if record is None:
            self._sync_window(day - timedelta(days=10), day + timedelta(days=10))
            record = self.repository.get(self.market_code, day)
        return bool(record.is_open) if record is not None else self._weekend_fallback(day)

    def normalize(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        self._sync_window(day - timedelta(days=15), day + timedelta(days=2))
        while not self.is_trading_day(day):
            day -= timedelta(days=1)
        return day

    def next_session(self, value: date | datetime) -> date:
        day = (value.date() if isinstance(value, datetime) else value) + timedelta(days=1)
        self._sync_window(day - timedelta(days=2), day + timedelta(days=20))
        while not self.is_trading_day(day):
            day += timedelta(days=1)
        return day

    def previous_session(self, value: date | datetime) -> date:
        return self.normalize((value.date() if isinstance(value, datetime) else value) - timedelta(days=1))

    def advance_sessions(self, value: date | datetime, sessions: int) -> date:
        day = self.normalize(value)
        self._sync_window(day, day + timedelta(days=max(sessions * 3 + 20, 30)))
        for _ in range(max(sessions, 0)):
            day = self.next_session(day)
        return day

    def _sync_window(self, start: date, end: date) -> None:
        unavailable_until = type(self)._remote_unavailable_until
        if unavailable_until and unavailable_until > datetime.now(UTC):
            return
        try:
            rows = self.bridge.get_history(
                symbols=["000001.SH"], period="1d", start_time=start.strftime("%Y%m%d"), end_time=end.strftime("%Y%m%d"),
                dividend_type="none", fill_data=False, prefer_cache_first=True,
            )
            open_days = {self._parse_day(item) for item in rows}
            open_days.discard(None)
            if not open_days:
                raise QmtBridgeError("empty trading calendar response")
            # QMT may return only data through the last completed session.  Do
            # not turn dates beyond that coverage into cached holidays; doing
            # so incorrectly normalizes a future Monday back to Friday.
            first_covered, last_covered = min(open_days), max(open_days)
            values = {
                day: day in open_days
                for offset in range((end - start).days + 1)
                if first_covered <= (day := start + timedelta(days=offset)) <= last_covered
            }
            self.repository.upsert_many(self.market_code, values, "qmt")
        except Exception:  # QMT may be unavailable in local/offline runs.
            type(self)._remote_unavailable_until = datetime.now(UTC) + timedelta(seconds=self.remote_retry_seconds)

    @staticmethod
    def _parse_day(row: dict) -> date | None:
        raw = row.get("time") or row.get("date") or row.get("trading_date") or row.get("trade_date")
        if raw is None:
            return None
        text = str(raw)
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8])) if text[:8].isdigit() else date.fromisoformat(text[:10])
        except ValueError:
            return None

    @staticmethod
    def _weekend_fallback(day: date) -> bool:
        return day.weekday() < 5

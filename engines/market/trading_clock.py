"""Injected business clock with no broker/QMT dependency."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")


class CalendarUnavailable(RuntimeError):
    code = "TRADING_CALENDAR_UNAVAILABLE"


class TradingCalendar(Protocol):
    degraded: bool
    def normalize(self, value: date | datetime) -> date: ...
    def is_trading_day(self, value: date) -> bool: ...
    def next_session(self, value: date | datetime) -> date: ...
    def previous_session(self, value: date | datetime) -> date: ...
    def advance_sessions(self, value: date | datetime, sessions: int) -> date: ...
    def session_times(self, value: date) -> tuple[time, time]: ...


class WeekdayTradingCalendar:
    """Explicit offline/test fallback; it does not claim holiday knowledge."""

    degraded = True

    def normalize(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day

    def is_trading_day(self, value: date) -> bool:
        return value.weekday() < 5

    def next_session(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day

    def previous_session(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day

    def advance_sessions(self, value: date | datetime, sessions: int) -> date:
        day = self.normalize(value)
        step = self.next_session if sessions >= 0 else self.previous_session
        for _ in range(abs(sessions)):
            day = step(day)
        return day

    def session_times(self, value: date) -> tuple[time, time]:
        return time(9, 30), time(15, 0)


# Compatibility name for legacy validators/tests. This is deliberately the
# QMT-free offline implementation; production composition should inject the
# quant-backed ``QuantTradingCalendarAdapter`` below.
ExchangeTradingCalendar = WeekdayTradingCalendar


@dataclass(frozen=True)
class CalendarSession:
    day: date
    is_open: bool = True
    open_time: time = time(9, 30)
    close_time: time = time(15, 0)


class SnapshotTradingCalendar:
    """Calendar backed by an explicit immutable snapshot/fixture."""

    degraded = False

    def __init__(self, sessions: list[CalendarSession] | tuple[CalendarSession, ...]) -> None:
        self.sessions = {item.day: item for item in sessions}

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]]) -> "SnapshotTradingCalendar":
        sessions = []
        for row in rows:
            raw = row.get("date") or row.get("trading_date") or row.get("trade_date")
            if raw is None:
                continue
            try:
                day = date.fromisoformat(str(raw)[:10]) if "-" in str(raw) else date(int(str(raw)[:4]), int(str(raw)[4:6]), int(str(raw)[6:8]))
            except (TypeError, ValueError):
                continue
            sessions.append(CalendarSession(day=day, is_open=bool(row.get("is_open", row.get("open", True))), open_time=_parse_time(row.get("open_time"), time(9, 30)), close_time=_parse_time(row.get("close_time"), time(15, 0))))
        return cls(sessions)

    def normalize(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        candidates = [item.day for item in self.sessions.values() if item.day <= day and item.is_open]
        if not candidates:
            raise CalendarUnavailable(f"calendar snapshot has no open session at or before {day}")
        return max(candidates)

    def is_trading_day(self, value: date) -> bool:
        item = self.sessions.get(value)
        return bool(item and item.is_open)

    def next_session(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        candidates = [item.day for item in self.sessions.values() if item.day > day and item.is_open]
        if not candidates:
            raise CalendarUnavailable(f"calendar snapshot has no next session after {day}")
        return min(candidates)

    def previous_session(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        candidates = [item.day for item in self.sessions.values() if item.day < day and item.is_open]
        if not candidates:
            raise CalendarUnavailable(f"calendar snapshot has no previous session before {day}")
        return max(candidates)

    def advance_sessions(self, value: date | datetime, sessions: int) -> date:
        day = self.normalize(value)
        step = self.next_session if sessions >= 0 else self.previous_session
        for _ in range(abs(sessions)):
            day = step(day)
        return day

    def session_times(self, value: date) -> tuple[time, time]:
        item = self.sessions.get(value)
        if item is None or not item.is_open:
            raise CalendarUnavailable(f"calendar snapshot has no open session for {value}")
        return item.open_time, item.close_time


class QuantTradingCalendarAdapter:
    """Read-only adapter around a caller-provided quant calendar fetcher."""

    degraded = False

    def __init__(self, fetch: Callable[[date, date], list[dict[str, Any]] | dict[str, Any]] | Any, *, market_code: str = "CN_A") -> None:
        self.fetch = fetch
        self.market_code = market_code
        self.snapshot = SnapshotTradingCalendar([])
        self._loaded_range: tuple[date, date] | None = None

    def _refresh(self, day: date) -> None:
        start, end = day - timedelta(days=370), day + timedelta(days=30)
        if self._loaded_range is not None and self._loaded_range[0] <= day <= self._loaded_range[1]:
            return
        if hasattr(self.fetch, "get_trading_calendar"):
            raw = self.fetch.get_trading_calendar(start.isoformat(), end.isoformat(), market_code=self.market_code)
        else:
            raw = self.fetch(start, end)
        if isinstance(raw, dict) and raw.get("contract_version") not in {None, "market-calendar.v1"}:
            raise CalendarUnavailable("trading calendar contract version mismatch")
        rows = raw.get("items", raw.get("sessions", [])) if isinstance(raw, dict) else raw
        self.snapshot = SnapshotTradingCalendar.from_rows(list(rows or []))
        self._loaded_range = (start, end)

    def is_trading_day(self, value: date) -> bool:
        self._refresh(value)
        return self.snapshot.is_trading_day(value)

    def next_session(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        self._refresh(day)
        try:
            return self.snapshot.next_session(day)
        except CalendarUnavailable:
            self._loaded_range = None
            self._refresh(day + timedelta(days=31))
            return self.snapshot.next_session(day)

    def previous_session(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        self._refresh(day)
        try:
            return self.snapshot.previous_session(day)
        except CalendarUnavailable:
            self._loaded_range = None
            self._refresh(day - timedelta(days=1))
            return self.snapshot.previous_session(day)

    def advance_sessions(self, value: date | datetime, sessions: int) -> date:
        day = self.normalize(value)
        step = self.next_session if sessions >= 0 else self.previous_session
        for _ in range(abs(sessions)):
            day = step(day)
        return day

    def normalize(self, value: date | datetime) -> date:
        day = value.date() if isinstance(value, datetime) else value
        self._refresh(day)
        return self.snapshot.normalize(day)

    def session_times(self, value: date) -> tuple[time, time]:
        self._refresh(value)
        return self.snapshot.session_times(value)


_DEFAULT_CLOCK: "TradingClock | None" = None


def configure_default_clock(clock: "TradingClock") -> None:
    global _DEFAULT_CLOCK
    _DEFAULT_CLOCK = clock


def get_default_clock() -> "TradingClock":
    global _DEFAULT_CLOCK
    if _DEFAULT_CLOCK is None:
        _DEFAULT_CLOCK = TradingClock()
    return _DEFAULT_CLOCK


class TradingClock:
    timezone = CN_TZ

    def __init__(self, *, calendar: TradingCalendar | None = None, now_fn: Callable[[], datetime] | None = None) -> None:
        self.calendar = calendar or WeekdayTradingCalendar()
        self._now_fn = now_fn

    @property
    def degraded(self) -> bool:
        return bool(getattr(self.calendar, "degraded", True))

    def now(self, market_code: str = "CN_A") -> datetime:
        value = self._now_fn() if self._now_fn is not None else datetime.now(UTC)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def calendar_date(self, value: datetime | None = None, market_code: str = "CN_A") -> date:
        return self.localize(value or self.now(market_code)).date()

    def current_trading_session(self, market_code: str = "CN_A", value: datetime | None = None) -> date:
        return self.calendar.normalize(self.calendar_date(value, market_code))

    def trading_session(self, value: datetime, market_code: str = "CN_A") -> date:
        return self.current_trading_session(market_code, value)

    def session_phase(self, value: datetime | None = None, market_code: str = "CN_A") -> str:
        instant = self.localize(value or self.now(market_code))
        day = self.calendar_date(instant, market_code)
        if hasattr(self.calendar, "is_trading_day") and not self.calendar.is_trading_day(day):
            return "CLOSED"
        session = self.current_trading_session(market_code, instant)
        opening, closing = self.calendar.session_times(session)
        current = instant.timetz().replace(tzinfo=None)
        if current < opening:
            return "PRE_OPEN"
        if current >= closing:
            return "AFTER_CLOSE"
        return "OPEN"

    def localize(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(self.timezone)

    def after_close(self, day: date, hour: int = 16) -> datetime:
        return datetime.combine(day, time(hour, 0), tzinfo=self.timezone).astimezone(UTC)


def _parse_time(value: Any, fallback: time) -> time:
    if value is None:
        return fallback
    try:
        return time.fromisoformat(str(value)[:8])
    except ValueError:
        return fallback

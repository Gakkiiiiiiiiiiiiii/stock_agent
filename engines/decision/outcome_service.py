"""D7 outcome service: read-only quant observations anchored to a decision."""
from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any, Protocol

from clients.quant_client import RemoteQuantClient
from contracts.outcome import DecisionOutcome
from contracts.proposal import _hash
from engines.market.trading_clock import QuantTradingCalendarAdapter, TradingClock, WeekdayTradingCalendar
from storage.repositories.research_repository import DecisionRepository, DecisionSnapshotRepository, OutcomeRepository


class QuantPriceSource(Protocol):
    def get_bars(self, symbols: list[str], start: str, end: str, *, adjust: str = "qfq") -> dict[str, Any]: ...


def _records(payload: Any, symbol: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    value = payload.get(symbol) or payload.get("records") or payload.get("rows") or payload.get("data") or payload.get("items") or []
    if isinstance(value, dict):
        value = value.get("records") or value.get("rows") or value.get("data") or []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    raw = str(value)
    try:
        return date.fromisoformat(raw[:10]) if "-" in raw else date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except (TypeError, ValueError):
        return None


class OutcomeService:
    def __init__(self, *, quant: QuantPriceSource | None = None, repository: OutcomeRepository | None = None, decisions: DecisionRepository | None = None, snapshots: DecisionSnapshotRepository | None = None, clock: Any | None = None, calendar: Any | None = None, offline_calendar: bool = False) -> None:
        self.quant = quant or RemoteQuantClient()
        self.repository = repository or OutcomeRepository()
        self.decisions = decisions or DecisionRepository()
        self.snapshots = snapshots or DecisionSnapshotRepository()
        # Do not call get_default_clock() here: its safe fallback calendar is
        # intentionally weekday/degraded and would mask the quant calendar.
        # The completely default service must construct the quant-backed
        # calendar first, then build its clock around that calendar.
        if calendar is not None:
            self.calendar = calendar
            self.clock = clock or TradingClock(calendar=self.calendar)
        elif clock is not None:
            self.clock = clock
            self.calendar = getattr(clock, "calendar", None)
            if self.calendar is None and offline_calendar:
                self.calendar = WeekdayTradingCalendar()
        elif offline_calendar:
            self.calendar = WeekdayTradingCalendar()
            self.clock = TradingClock(calendar=self.calendar)
        else:
            self.calendar = QuantTradingCalendarAdapter(self.quant)
            self.clock = TradingClock(calendar=self.calendar)

    def _now(self) -> datetime:
        value = self.clock() if callable(self.clock) else self.clock.now() if self.clock is not None and hasattr(self.clock, "now") else datetime.now(UTC)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("outcome clock must return timezone-aware datetime")
        return value

    def refresh(self, *, decision_id: str, horizon: str, measured_at: datetime) -> DecisionOutcome:
        decision = self.decisions.get(decision_id)
        if decision is None:
            raise ValueError("DECISION_NOT_FOUND")
        natural_key = _hash({
            "decision_id": decision_id,
            "horizon": str(horizon).strip().upper(),
            "measured_at": measured_at.astimezone(UTC).isoformat() if measured_at.tzinfo is not None else str(measured_at),
        })
        stable_outcome_id = f"outcome-{natural_key[:32]}"
        existing = self.repository.get(stable_outcome_id)
        if existing is not None:
            return existing
        snapshot = self.snapshots.get_v3_for_decision(decision_id)
        decision_as_of = decision.decision_as_of or decision.created_at
        if decision_as_of is not None and decision_as_of.tzinfo is None:
            decision_as_of = decision_as_of.replace(tzinfo=UTC)
        if decision_as_of is None:
            raise ValueError("DECISION_AS_OF_REQUIRED")
        if measured_at.tzinfo is None or measured_at.utcoffset() is None:
            raise ValueError("measured_at must be timezone-aware")
        symbols = [str(item.get("symbol")) for item in (decision.candidates or []) if isinstance(item, dict) and item.get("symbol")]
        if not symbols:
            raise ValueError("DECISION_HAS_NO_SYMBOL_CANDIDATES")
        entry_date = self._next_session(decision_as_of.date())
        exit_date = measured_at.date()
        if horizon.upper() in {"T+1", "1D", "NEXT_SESSION"} and exit_date != entry_date:
            raise ValueError("T+1 outcome must be measured on the next trading session")
        benchmark = str(decision.benchmark_symbol or "000001.SH")
        requested = list(dict.fromkeys(symbols + [benchmark]))
        payload = self.quant.get_bars(requested, entry_date.isoformat(), exit_date.isoformat())
        returns: list[float] = []
        source_refs: list[str] = []
        for symbol in symbols:
            rows = self._usable_rows(_records(payload, symbol), measured_at, payload.get("available_at") if isinstance(payload, dict) else None)
            if not rows:
                raise ValueError(f"OUTCOME_PRICE_UNAVAILABLE:{symbol}")
            entry = next((row for row in rows if _day(row.get("date") or row.get("time")) == entry_date and row.get("open") is not None), None)
            end = next((row for row in reversed(rows) if _day(row.get("date") or row.get("time")) and _day(row.get("date") or row.get("time")) <= exit_date and row.get("close") is not None), None)
            if entry is None or end is None or float(entry["open"]) <= 0:
                raise ValueError(f"OUTCOME_PRICE_UNAVAILABLE:{symbol}")
            returns.append(float(end["close"]) / float(entry["open"]) - 1.0)
            source_refs.append(str(end.get("snapshot_id") or end.get("source_ref") or f"quant:bars:{symbol}:{entry_date}:{exit_date}"))
        benchmark_rows = self._usable_rows(_records(payload, benchmark), measured_at, payload.get("available_at") if isinstance(payload, dict) else None)
        benchmark_return = self._return(benchmark_rows, entry_date, exit_date)
        if benchmark_return is not None:
            source_refs.append(f"quant:bars:{benchmark}:{entry_date}:{exit_date}")
        absolute = sum(returns) / len(returns)
        attribution = {
            "source_system": "quant", "source_snapshot_refs": source_refs,
            "contract_version": str(payload.get("contract_version") if isinstance(payload, dict) else "market-data.v1"),
            "service_version": payload.get("service_version") if isinstance(payload, dict) else None,
            "data_version": payload.get("data_version") if isinstance(payload, dict) else None,
            "decision_snapshot_id": snapshot.snapshot_id if snapshot is not None else None,
        }
        raw_available = payload.get("available_at") if isinstance(payload, dict) else None
        available_at = measured_at
        if raw_available is not None:
            available_at = datetime.fromisoformat(str(raw_available).replace("Z", "+00:00"))
        item = DecisionOutcome.build(
            outcome_id=stable_outcome_id, decision_id=decision_id, horizon=horizon,
            measured_at=measured_at, available_at=available_at, decision_as_of=decision_as_of,
            return_pct=absolute, benchmark_return_pct=benchmark_return,
            excess_return_pct=absolute - benchmark_return if benchmark_return is not None else None,
            invalidation_hit=False, source_system="quant", source_snapshot_refs=source_refs,
            attribution=attribution,
        )
        row = self.repository.save(item)
        return DecisionOutcome.model_validate(row.payload_json)

    def _next_session(self, day: date) -> date:
        if self.calendar is not None:
            if hasattr(self.calendar, "next_session"):
                return self.calendar.next_session(day)
            if hasattr(self.calendar, "advance_sessions"):
                return self.calendar.advance_sessions(day, 1)
            # D10's shared TradingCalendar intentionally exposes the smaller
            # normalize/is_trading_day protocol.  Resolve the next open
            # session here instead of silently replacing it with weekdays.
            if hasattr(self.calendar, "is_trading_day"):
                current = day
                for _ in range(370):
                    current = date.fromordinal(current.toordinal() + 1)
                    if self.calendar.is_trading_day(current):
                        return current
                raise ValueError("TRADING_CALENDAR_UNAVAILABLE")
            raise ValueError("TRADING_CALENDAR_UNAVAILABLE")
        raise ValueError("TRADING_CALENDAR_UNAVAILABLE")

    @staticmethod
    def _usable_rows(rows: list[dict[str, Any]], measured_at: datetime, default_available: Any = None) -> list[dict[str, Any]]:
        usable = []
        for row in rows:
            available = row.get("available_at") or row.get("availableAt") or default_available
            if available is None:
                raise ValueError("quant response missing available_at")
            try:
                timestamp = datetime.fromisoformat(str(available).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("quant response has invalid available_at") from exc
            if timestamp.tzinfo is None or timestamp > measured_at:
                raise ValueError("quant response is not available by measured_at")
            usable.append(row)
        return usable

    @staticmethod
    def _return(rows: list[dict[str, Any]], entry_date: date, exit_date: date) -> float | None:
        entry = next((row for row in rows if _day(row.get("date") or row.get("time")) == entry_date and row.get("open") is not None), None)
        end = next((row for row in reversed(rows) if _day(row.get("date") or row.get("time")) and _day(row.get("date") or row.get("time")) <= exit_date and row.get("close") is not None), None)
        if entry is None or end is None or float(entry["open"]) <= 0:
            return None
        return float(end["close"]) / float(entry["open"]) - 1.0

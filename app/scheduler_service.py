from __future__ import annotations

from datetime import UTC, datetime, time

from engines.market.market_clock import MarketClock
from storage.repositories.job_repository import JobTaskRepository


class SchedulerService:
    """Small, idempotent scheduler entrypoint for externally invoked cron ticks."""

    def __init__(self, repository: JobTaskRepository | None = None, clock: MarketClock | None = None) -> None:
        self.repository = repository or JobTaskRepository()
        self.clock = clock or MarketClock()

    def tick(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        local = self.clock.localize(current)
        if local.weekday() >= 5 or local.time() < time(16, 15):
            return []
        session_day = self.clock.calendar_date(current)
        task = self.repository.create(
            "memory_lifecycle_sweep",
            {"now": current.astimezone(UTC).isoformat()},
            idempotency_key=f"memory-lifecycle:{session_day.isoformat()}",
            not_before=current.astimezone(UTC),
        )
        return [task["id"]]

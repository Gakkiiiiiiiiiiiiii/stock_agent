from __future__ import annotations

from datetime import UTC, datetime, timedelta

from storage.repositories.vector_repository import MemoryRepository


MEMORY_TTL_DAYS = {"MARKET_REGIME": 30, "THEME": 90}


class MemoryLifecycleService:
    def __init__(self, repository: MemoryRepository | None = None) -> None:
        self.repository = repository or MemoryRepository()

    def expire_due(self, now: datetime | None = None, limit: int = 500) -> dict:
        now = now or datetime.now(UTC)
        expired: list[int] = []
        for record in self.repository.list_all()[:limit]:
            if str(record.status).upper() in {"SUPERSEDED", "EXPIRED", "REJECTED"}:
                continue
            valid_to = record.valid_to
            ttl = MEMORY_TTL_DAYS.get(str(record.memory_type).upper())
            due = valid_to or (record.last_seen_at + timedelta(days=ttl) if ttl and record.last_seen_at else None)
            if due and due.replace(tzinfo=UTC) <= now:
                self.repository.update(record.id, status="EXPIRED", valid_to=due)
                expired.append(record.id)
        return {"expired_memory_ids": expired, "count": len(expired)}

    def validate(self, memory_id: int) -> dict:
        record = self.repository.update(memory_id, status="VALIDATED")
        return {"memory_id": record.id, "status": record.status}

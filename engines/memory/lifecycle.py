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

    def record_outcome_evidence(self, memory_id: int, excess_return: float) -> dict:
        """Update strategy-memory confidence from an independent decision outcome.

        The counters deliberately reset on an opposite outcome: the lifecycle is
        driven by consecutive evidence, rather than a lifetime total that could
        hide a recently invalid strategy assumption.
        """
        record = self.repository.get(memory_id)
        if record is None:
            raise FileNotFoundError(memory_id)
        metadata = dict(record.metadata_json or {})
        supported = float(excess_return) >= 0
        if supported:
            support_count = int(metadata.get("outcome_support_count", 0)) + 1
            failure_count = 0
        else:
            support_count = 0
            failure_count = int(metadata.get("outcome_failure_count", 0)) + 1
        metadata.update(
            {
                "outcome_support_count": support_count,
                "outcome_failure_count": failure_count,
                "last_outcome_excess_return": float(excess_return),
                "last_outcome_at": datetime.now(UTC).isoformat(),
            }
        )
        status = record.status
        if support_count >= 3:
            status = "VALIDATED"
        elif failure_count >= 3:
            status = "REVALIDATION_REQUIRED"
        saved = self.repository.update(memory_id, metadata_json=metadata, status=status)
        return {
            "memory_id": saved.id,
            "status": saved.status,
            "outcome_support_count": support_count,
            "outcome_failure_count": failure_count,
        }

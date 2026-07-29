from __future__ import annotations

from datetime import UTC, datetime

from storage.repositories.knowledge_repository import KnowledgeRepository, KnowledgeVectorTaskService


class KnowledgeLifecycleService:
    VALID_LIFECYCLE_STATUSES = {
        "EXTRACTED",
        "ACTIVE",
        "VALIDATED",
        "SUPERSEDED",
        "EXPIRED",
        "REJECTED",
        "RETIRED",
    }
    VALID_VERIFICATION_STATUSES = {
        "UNVERIFIED",
        "SOURCE_CONFIRMED",
        "VERIFIED",
        "VALIDATED",
        "REJECTED",
        "NEEDS_REVIEW",
    }
    TERMINAL_STATUSES = {"REJECTED", "RETIRED"}

    def __init__(
        self,
        repository: KnowledgeRepository | None = None,
        vector_task_service: KnowledgeVectorTaskService | None = None,
    ) -> None:
        self.repository = repository or KnowledgeRepository()
        self.vector_task_service = vector_task_service or KnowledgeVectorTaskService()

    def transition_unit(
        self,
        unit_id: int,
        *,
        lifecycle_status: str | None = None,
        verification_status: str | None = None,
        valid_to: datetime | None = None,
        reason: str | None = None,
        operator: str | None = None,
    ) -> dict | None:
        lifecycle_status = self._normalize_status(lifecycle_status, self.VALID_LIFECYCLE_STATUSES, "lifecycle_status")
        verification_status = self._normalize_status(verification_status, self.VALID_VERIFICATION_STATUSES, "verification_status")
        unit = self.repository.update_unit_lifecycle(
            unit_id,
            lifecycle_status=lifecycle_status,
            verification_status=verification_status,
            valid_to=valid_to,
            reason=reason,
            operator=operator or "manual",
        )
        if unit is None:
            return None
        vector_tasks = self.vector_task_service.enqueue_unit_sync(unit, delete=unit.get("lifecycle_status") in self.TERMINAL_STATUSES)
        self._record_vector_tasks(unit, vector_tasks)
        return unit | {"vector_tasks": vector_tasks}

    def expire_due_units(self, now: datetime | None = None, limit: int = 500) -> dict:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        units = self.repository.expire_due_units(now=now, limit=limit)
        items = []
        vector_tasks = []
        for unit in units:
            tasks = self.vector_task_service.enqueue_unit_sync(unit)
            self._record_vector_tasks(unit, tasks)
            items.append(unit | {"vector_tasks": tasks})
            vector_tasks.extend(tasks)
        return {"expired_count": len(items), "items": items, "vector_tasks": vector_tasks, "as_of": now.isoformat()}

    def sync_vector_for_lifecycle_change(self, unit_id: int) -> dict | None:
        unit = self.repository.get_unit(unit_id)
        if unit is None:
            return None
        vector_tasks = self.vector_task_service.enqueue_unit_sync(unit, delete=unit.get("lifecycle_status") in self.TERMINAL_STATUSES)
        return unit | {"vector_tasks": vector_tasks}

    def list_conflicts(self, subject_key: str | None = None, limit: int = 50) -> dict:
        return {"items": self.repository.list_conflicts(subject_key=subject_key, limit=limit), "limit": limit}

    def list_unit_audits(self, unit_id: int, limit: int = 50) -> dict:
        return {"knowledge_unit_id": unit_id, "items": self.repository.list_unit_lifecycle_audits(unit_id, limit=limit), "limit": limit}

    def _record_vector_tasks(self, unit: dict, vector_tasks: list[dict]) -> None:
        audit = unit.get("lifecycle_audit") or {}
        audit_id = audit.get("id")
        if audit_id:
            self.repository.record_lifecycle_vector_tasks(int(audit_id), vector_tasks)
            audit["vector_task_ids"] = [task.get("task_id") for task in vector_tasks if task.get("task_id")]

    @classmethod
    def _normalize_status(cls, value: str | None, allowed: set[str], field: str) -> str | None:
        if value in (None, ""):
            return None
        normalized = str(value).upper()
        if normalized not in allowed:
            raise ValueError(f"invalid {field}: {value}")
        return normalized

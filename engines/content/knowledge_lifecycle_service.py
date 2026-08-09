from __future__ import annotations

from datetime import UTC, datetime

from engines.content.knowledge_enums import (
    LifecycleStatus,
    ReviewStatus,
    VerificationStatus,
)
from storage.repositories.knowledge_repository import KnowledgeRepository, KnowledgeVectorTaskService


class KnowledgeLifecycleService:
    # 枚举统一（P0-11 / 设计文档 §36）：全部从 knowledge_enums 派生，禁止本地另写一份 set。
    VALID_LIFECYCLE_STATUSES = LifecycleStatus.values()
    # verification_status 为 deprecated 兼容字段（见 knowledge_enums.VerificationStatus）。
    VALID_VERIFICATION_STATUSES = VerificationStatus.values()
    VALID_REVIEW_STATUSES = ReviewStatus.values()
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
        review_status: str | None = None,
        valid_to: datetime | None = None,
        reason: str | None = None,
        operator: str | None = None,
    ) -> dict | None:
        """人工生命周期/审核 transition（P0-12 状态机，设计文档 §37-39）。

        联动规则：
        - lifecycle 进入终态（REJECTED/RETIRED）→ enqueue vector delete（现状保留）。
        - review_status=REJECTED（即使 lifecycle 不变）→ 必须 enqueue vector delete，
          主动从 Qdrant 删除旧向量（is_indexable 的 review gate 只挡新写入）。
        - review_status=APPROVED/OVERRIDDEN 只记录，不改 support_status：
          人工不能伪造机器证据判断（§39）。OVERRIDDEN 必须带 operator + reason。
        - 每次 transition 写入一条 MANUAL_REVIEW verification ledger 记录。
        - verification_status 参数 deprecated，仅为兼容保留。
        """
        lifecycle_status = self._normalize_status(lifecycle_status, self.VALID_LIFECYCLE_STATUSES, "lifecycle_status")
        verification_status = self._normalize_status(verification_status, self.VALID_VERIFICATION_STATUSES, "verification_status")
        review_status = self._normalize_status(review_status, self.VALID_REVIEW_STATUSES, "review_status")
        if review_status == "OVERRIDDEN" and (not operator or not reason):
            raise ValueError("review_status=OVERRIDDEN requires operator and reason")
        before = self.repository.get_unit(unit_id)
        if before is None:
            return None
        unit = self.repository.update_unit_lifecycle(
            unit_id,
            lifecycle_status=lifecycle_status,
            verification_status=verification_status,
            review_status=review_status,
            valid_to=valid_to,
            reason=reason,
            operator=operator or "manual",
        )
        if unit is None:
            return None
        review_rejected = str(unit.get("review_status") or "").upper() == "REJECTED"
        delete = unit.get("lifecycle_status") in self.TERMINAL_STATUSES or review_rejected
        vector_tasks = self.vector_task_service.enqueue_unit_sync(unit, delete=delete)
        self._record_vector_tasks(unit, vector_tasks)
        self._record_manual_review(
            unit,
            review_status=review_status,
            transition_status=review_status or lifecycle_status or verification_status,
            before=before,
            reason=reason,
            operator=operator or "manual",
        )
        return unit | {"vector_tasks": vector_tasks}

    def _record_manual_review(
        self,
        unit: dict,
        *,
        review_status: str | None,
        transition_status: str | None,
        before: dict,
        reason: str | None,
        operator: str,
    ) -> None:
        """把每次人工 transition 写入 verification ledger（P0-12 / §38-39）。"""
        detail = {
            "operator": operator,
            "reason": reason,
            "from": {
                "lifecycle_status": before.get("lifecycle_status"),
                "verification_status": before.get("verification_status"),
                "review_status": before.get("review_status"),
            },
            "to": {
                "lifecycle_status": unit.get("lifecycle_status"),
                "verification_status": unit.get("verification_status"),
                "review_status": unit.get("review_status"),
            },
        }
        self.repository.append_verification(
            int(unit["id"]),
            verifier_type="MANUAL_REVIEW",
            status=str(review_status or transition_status or "TRANSITION"),
            detail=detail,
            provider="manual",
        )

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

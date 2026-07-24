from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from storage.db import session_scope


class JobTaskRepository:
    def create(
        self,
        task_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        if idempotency_key:
            existing = self.get_by_idempotency_key(idempotency_key)
            if existing:
                return existing
        task_id = str(uuid.uuid4())
        with session_scope() as session:
            session.execute(
                text(
                    """
                    INSERT INTO job_task
                    (id, task_type, payload, status, progress, retry_count, max_retries, idempotency_key, created_at)
                    VALUES (:id, :task_type, :payload, 'PENDING', 0, 0, :max_retries, :idempotency_key, :created_at)
                    """
                ),
                {
                    "id": task_id,
                    "task_type": task_type,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "max_retries": max_retries,
                    "idempotency_key": idempotency_key,
                    "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
                },
            )
        return self.get(task_id) or {"id": task_id, "status": "PENDING"}

    def get(self, task_id: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.execute(text("SELECT * FROM job_task WHERE id=:id"), {"id": task_id}).mappings().first()
            return self._row(row)

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with session_scope() as session:
            row = session.execute(text("SELECT * FROM job_task WHERE idempotency_key=:key"), {"key": key}).mappings().first()
            return self._row(row)

    def heartbeat(self, task_id: str, worker_id: str, progress: float | None = None) -> None:
        values = {"id": task_id, "worker_id": worker_id, "heartbeat_at": datetime.now(timezone.utc).replace(tzinfo=None)}
        progress_sql = ""
        if progress is not None:
            values["progress"] = progress
            progress_sql = ", progress=:progress"
        with session_scope() as session:
            session.execute(
                text(f"UPDATE job_task SET worker_id=:worker_id, heartbeat_at=:heartbeat_at{progress_sql} WHERE id=:id"),
                values,
            )

    def mark_running(self, task_id: str, worker_id: str) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope() as session:
            session.execute(
                text("UPDATE job_task SET status='RUNNING', worker_id=:worker_id, started_at=:now, heartbeat_at=:now WHERE id=:id"),
                {"id": task_id, "worker_id": worker_id, "now": now},
            )

    def mark_finished(self, task_id: str, status: str, result_ref: str | None = None, error: dict | None = None) -> None:
        with session_scope() as session:
            session.execute(
                text(
                    """
                    UPDATE job_task
                    SET status=:status, result_ref=:result_ref, error=:error, finished_at=:finished_at, progress=:progress
                    WHERE id=:id
                    """
                ),
                {
                    "id": task_id,
                    "status": status,
                    "result_ref": result_ref,
                    "error": json.dumps(error, ensure_ascii=False) if error else None,
                    "finished_at": datetime.now(timezone.utc).replace(tzinfo=None),
                    "progress": 1 if status == "SUCCEEDED" else 0,
                },
            )

    @staticmethod
    def _row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        for key in ("payload", "error"):
            if isinstance(data.get(key), str) and data[key]:
                try:
                    data[key] = json.loads(data[key])
                except json.JSONDecodeError:
                    pass
        return data

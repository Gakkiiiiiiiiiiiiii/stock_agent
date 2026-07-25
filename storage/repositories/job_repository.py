from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import inspect, text

from storage.db import session_scope


class JobTaskRepository:
    _schema_ready = False

    def create(
        self,
        task_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        self._ensure_schema()
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
        self._ensure_schema()
        with session_scope() as session:
            row = session.execute(text("SELECT * FROM job_task WHERE id=:id"), {"id": task_id}).mappings().first()
            return self._row(row)

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        self._ensure_schema()
        with session_scope() as session:
            row = session.execute(text("SELECT * FROM job_task WHERE idempotency_key=:key"), {"key": key}).mappings().first()
            return self._row(row)

    def heartbeat(
        self,
        task_id: str,
        worker_id: str,
        progress: float | None = None,
        lease_token: str | None = None,
        lease_version: int | None = None,
    ) -> bool:
        self._ensure_schema()
        values = {"id": task_id, "worker_id": worker_id, "heartbeat_at": datetime.now(timezone.utc).replace(tzinfo=None)}
        lease_clause = ""
        if lease_token is not None:
            values["lease_token"] = lease_token
            lease_clause += " AND lease_token=:lease_token"
        if lease_version is not None:
            values["lease_version"] = lease_version
            lease_clause += " AND lease_version=:lease_version"
        progress_sql = ""
        if progress is not None:
            values["progress"] = progress
            progress_sql = ", progress=:progress"
        with session_scope() as session:
            result = session.execute(
                text(f"UPDATE job_task SET heartbeat_at=:heartbeat_at{progress_sql} WHERE id=:id AND status='RUNNING' AND worker_id=:worker_id{lease_clause}"),
                values,
            )
            return bool(result.rowcount)

    def claim_next(self, worker_id: str, task_types: list[str] | None = None, lease_seconds: int = 300) -> dict[str, Any] | None:
        self._ensure_schema()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lease_cutoff = now - timedelta(seconds=max(int(lease_seconds), 1))
        lease_token = secrets.token_hex(16)
        with session_scope() as session:
            filters = "(status IN ('PENDING', 'FAILED_RETRYABLE') OR (status='RUNNING' AND heartbeat_at < :lease_cutoff))"
            params: dict[str, Any] = {"worker_id": worker_id, "now": now, "lease_cutoff": lease_cutoff, "lease_token": lease_token}
            if task_types:
                keys = []
                for index, task_type in enumerate(task_types):
                    key = f"task_type_{index}"
                    params[key] = task_type
                    keys.append(f":{key}")
                filters += f" AND task_type IN ({', '.join(keys)})"
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "postgresql":
                row = session.execute(
                    text(
                        f"""
                        UPDATE job_task
                        SET status='RUNNING',
                            worker_id=:worker_id,
                            started_at=coalesce(started_at, :now),
                            heartbeat_at=:now,
                            lease_token=:lease_token,
                            lease_version=coalesce(lease_version, 0) + 1
                        WHERE id = (
                            SELECT id FROM job_task
                            WHERE {filters}
                            ORDER BY created_at ASC
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        RETURNING *
                        """
                    ),
                    params,
                ).mappings().first()
                return self._row(row)
            row = session.execute(
                text(f"SELECT * FROM job_task WHERE {filters} ORDER BY created_at ASC LIMIT 1"),
                params,
            ).mappings().first()
            if row is None:
                return None
            result = session.execute(
                text(
                    """
                    UPDATE job_task
                    SET status='RUNNING',
                        worker_id=:worker_id,
                        started_at=coalesce(started_at, :now),
                        heartbeat_at=:now,
                        lease_token=:lease_token,
                        lease_version=coalesce(lease_version, 0) + 1
                    WHERE id=:id
                      AND (status IN ('PENDING', 'FAILED_RETRYABLE') OR (status='RUNNING' AND heartbeat_at < :lease_cutoff))
                    """
                ),
                params | {"id": row["id"]},
            )
            if not result.rowcount:
                return None
            return (self._row(row) or {}) | {
                "status": "RUNNING",
                "worker_id": worker_id,
                "started_at": row.get("started_at") or now,
                "heartbeat_at": now,
                "lease_token": lease_token,
                "lease_version": int(row.get("lease_version") or 0) + 1,
            }

    def claim(self, task_id: str, worker_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lease_token = secrets.token_hex(16)
        with session_scope() as session:
            result = session.execute(
                text(
                    """
                    UPDATE job_task
                    SET status='RUNNING',
                        worker_id=:worker_id,
                        started_at=:now,
                        heartbeat_at=:now,
                        lease_token=:lease_token,
                        lease_version=coalesce(lease_version, 0) + 1
                    WHERE id=:id AND status IN ('PENDING', 'FAILED_RETRYABLE')
                    """
                ),
                {"id": task_id, "worker_id": worker_id, "now": now, "lease_token": lease_token},
            )
            if not result.rowcount:
                return None
        task = self.get(task_id)
        return task if task and task.get("status") == "RUNNING" else None

    def mark_running(self, task_id: str, worker_id: str) -> None:
        self._ensure_schema()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope() as session:
            session.execute(
                text("UPDATE job_task SET status='RUNNING', worker_id=:worker_id, started_at=:now, heartbeat_at=:now WHERE id=:id"),
                {"id": task_id, "worker_id": worker_id, "now": now},
            )

    def has_lease(self, task_id: str, worker_id: str, lease_token: str | None, lease_version: int | None) -> bool:
        self._ensure_schema()
        if not lease_token or lease_version is None:
            return False
        with session_scope() as session:
            row = session.execute(
                text(
                    """
                    SELECT id FROM job_task
                    WHERE id=:id AND status='RUNNING' AND worker_id=:worker_id
                      AND lease_token=:lease_token AND lease_version=:lease_version
                    """
                ),
                {
                    "id": task_id,
                    "worker_id": worker_id,
                    "lease_token": lease_token,
                    "lease_version": lease_version,
                },
            ).first()
            return row is not None

    def mark_finished(
        self,
        task_id: str,
        status: str,
        result_ref: str | None = None,
        error: dict | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
        lease_version: int | None = None,
    ) -> bool:
        self._ensure_schema()
        owner_clause = " AND worker_id=:worker_id" if worker_id else ""
        lease_clause = ""
        if lease_token is not None:
            lease_clause += " AND lease_token=:lease_token"
        if lease_version is not None:
            lease_clause += " AND lease_version=:lease_version"
        with session_scope() as session:
            result = session.execute(
                text(
                    f"""
                    UPDATE job_task
                    SET status=:status, result_ref=:result_ref, error=:error, finished_at=:finished_at, progress=:progress
                    WHERE id=:id AND status='RUNNING'{owner_clause}{lease_clause}
                    """
                ),
                {
                    "id": task_id,
                    "worker_id": worker_id,
                    "status": status,
                    "result_ref": result_ref,
                    "error": json.dumps(error, ensure_ascii=False) if error else None,
                    "finished_at": datetime.now(timezone.utc).replace(tzinfo=None),
                    "progress": 1 if status == "SUCCEEDED" else 0,
                    "lease_token": lease_token,
                    "lease_version": lease_version,
                },
            )
            return bool(result.rowcount)

    def mark_failed(
        self,
        task_id: str,
        error: dict | str,
        worker_id: str | None = None,
        lease_token: str | None = None,
        lease_version: int | None = None,
    ) -> bool:
        self._ensure_schema()
        task = self.get(task_id)
        if task is None:
            return False
        if worker_id and task.get("worker_id") != worker_id:
            return False
        if lease_token is not None and task.get("lease_token") != lease_token:
            return False
        if lease_version is not None and int(task.get("lease_version") or 0) != int(lease_version):
            return False
        retry_count = int(task.get("retry_count") or 0) + 1
        max_retries = int(task.get("max_retries") or 0)
        status = "FAILED_RETRYABLE" if retry_count <= max_retries else "FAILED_FINAL"
        owner_clause = " AND worker_id=:worker_id" if worker_id else ""
        lease_clause = ""
        if lease_token is not None:
            lease_clause += " AND lease_token=:lease_token"
        if lease_version is not None:
            lease_clause += " AND lease_version=:lease_version"
        with session_scope() as session:
            result = session.execute(
                text(
                    f"""
                    UPDATE job_task
                    SET status=:status, error=:error, retry_count=:retry_count, finished_at=:finished_at, progress=0
                    WHERE id=:id AND status='RUNNING'{owner_clause}{lease_clause}
                    """
                ),
                {
                    "id": task_id,
                    "worker_id": worker_id,
                    "status": status,
                    "error": json.dumps(error, ensure_ascii=False) if not isinstance(error, str) else error,
                    "retry_count": retry_count,
                    "finished_at": datetime.now(timezone.utc).replace(tzinfo=None),
                    "lease_token": lease_token,
                    "lease_version": lease_version,
                },
            )
            return bool(result.rowcount)

    def cancel(self, task_id: str) -> bool:
        self._ensure_schema()
        with session_scope() as session:
            result = session.execute(text("UPDATE job_task SET status='CANCELLED' WHERE id=:id AND status IN ('PENDING', 'FAILED_RETRYABLE')"), {"id": task_id})
            return bool(result.rowcount)

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

    @classmethod
    def _ensure_schema(cls) -> None:
        if cls._schema_ready:
            return
        with session_scope() as session:
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS job_task (
                        id VARCHAR(36) PRIMARY KEY,
                        task_type VARCHAR(64) NOT NULL,
                        payload TEXT NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        progress NUMERIC(8,4) NOT NULL DEFAULT 0,
                        result_ref TEXT,
                        error TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        max_retries INTEGER NOT NULL DEFAULT 3,
                        idempotency_key VARCHAR(128),
                        worker_id VARCHAR(128),
                        lease_token VARCHAR(64),
                        lease_version BIGINT NOT NULL DEFAULT 0,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        started_at TIMESTAMP,
                        heartbeat_at TIMESTAMP,
                        finished_at TIMESTAMP
                    )
                    """
                )
            )
            session.execute(text("CREATE INDEX IF NOT EXISTS idx_job_task_status ON job_task(status, created_at)"))
            existing_columns = {column["name"] for column in inspect(session.bind).get_columns("job_task")}
            if "lease_token" not in existing_columns:
                session.execute(text("ALTER TABLE job_task ADD COLUMN lease_token VARCHAR(64)"))
            if "lease_version" not in existing_columns:
                session.execute(text("ALTER TABLE job_task ADD COLUMN lease_version BIGINT NOT NULL DEFAULT 0"))
        cls._schema_ready = True

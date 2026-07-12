from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from storage.db import session_scope
from storage.models.vector import MemoryRecord, VectorIndexMapping, VectorIndexTask


class VectorTaskRepository:
    def enqueue(self, postgres_table: str, postgres_id: int, target_collection: str, task_type: str = "upsert") -> VectorIndexTask:
        with session_scope() as session:
            task = VectorIndexTask(
                task_type=task_type,
                postgres_table=postgres_table,
                postgres_id=postgres_id,
                target_collection=target_collection,
                status="pending",
            )
            session.add(task)
            session.flush()
            session.refresh(task)
            return task

    def next_pending(self) -> VectorIndexTask | None:
        with session_scope() as session:
            task = session.execute(
                select(VectorIndexTask).where(VectorIndexTask.status == "pending").order_by(VectorIndexTask.created_at.asc())
            ).scalars().first()
            if task:
                task.status = "processing"
                task.updated_at = datetime.now(UTC)
                session.add(task)
                session.flush()
                session.refresh(task)
            return task

    def mark_success(self, task_id: int) -> None:
        with session_scope() as session:
            task = session.get(VectorIndexTask, task_id)
            if task:
                task.status = "success"
                task.updated_at = datetime.now(UTC)
                session.add(task)

    def mark_failed(self, task_id: int, error_message: str) -> None:
        with session_scope() as session:
            task = session.get(VectorIndexTask, task_id)
            if task:
                task.status = "failed"
                task.error_message = error_message
                task.retry_count += 1
                task.updated_at = datetime.now(UTC)
                session.add(task)


class MemoryRepository:
    def create(self, **kwargs) -> MemoryRecord:
        with session_scope() as session:
            record = MemoryRecord(**kwargs)
            session.add(record)
            session.flush()
            session.refresh(record)
            return record

    def update(self, record_id: int, **kwargs) -> MemoryRecord:
        with session_scope() as session:
            record = session.get(MemoryRecord, record_id)
            if record is None:
                raise FileNotFoundError(record_id)
            for key, value in kwargs.items():
                setattr(record, key, value)
            session.add(record)
            session.flush()
            session.refresh(record)
            return record

    def get(self, record_id: int) -> MemoryRecord | None:
        with session_scope() as session:
            return session.get(MemoryRecord, record_id)

    def get_by_title_source_type(self, title: str, source_type: str) -> MemoryRecord | None:
        with session_scope() as session:
            return session.execute(
                select(MemoryRecord).where(
                    MemoryRecord.title == title,
                    MemoryRecord.source_type == source_type,
                )
            ).scalars().first()

    def list_by_title_prefix(self, source_type: str, title_prefix: str) -> list[MemoryRecord]:
        with session_scope() as session:
            return list(
                session.execute(
                    select(MemoryRecord).where(
                        MemoryRecord.source_type == source_type,
                        MemoryRecord.title.like(f"{title_prefix}%"),
                    )
                ).scalars()
            )

    def mark_deleted(self, record_id: int) -> MemoryRecord | None:
        with session_scope() as session:
            record = session.get(MemoryRecord, record_id)
            if record is None:
                return None
            record.is_deleted = True
            session.add(record)
            session.flush()
            session.refresh(record)
            return record

    def list_all(self) -> list[MemoryRecord]:
        with session_scope() as session:
            return list(session.execute(select(MemoryRecord).order_by(MemoryRecord.id.asc())).scalars())


class VectorMappingRepository:
    def list_for_record(self, postgres_table: str, postgres_id: int) -> list[VectorIndexMapping]:
        with session_scope() as session:
            return list(
                session.execute(
                    select(VectorIndexMapping).where(
                        VectorIndexMapping.postgres_table == postgres_table,
                        VectorIndexMapping.postgres_id == postgres_id,
                    )
                ).scalars()
            )

    def delete_for_record(self, postgres_table: str, postgres_id: int) -> None:
        with session_scope() as session:
            rows = session.execute(
                select(VectorIndexMapping).where(
                    VectorIndexMapping.postgres_table == postgres_table,
                    VectorIndexMapping.postgres_id == postgres_id,
                )
            ).scalars()
            for row in rows:
                session.delete(row)

    def upsert(
        self,
        postgres_table: str,
        postgres_id: int,
        chunk_id: str,
        qdrant_collection: str,
        qdrant_point_id: str,
        content_hash: str,
        embedding_model: str,
        reranker_model: str,
    ) -> VectorIndexMapping:
        with session_scope() as session:
            existing = session.execute(
                select(VectorIndexMapping).where(
                    VectorIndexMapping.postgres_table == postgres_table,
                    VectorIndexMapping.postgres_id == postgres_id,
                    VectorIndexMapping.chunk_id == chunk_id,
                )
            ).scalars().first()
            if existing is None:
                existing = VectorIndexMapping(
                    postgres_table=postgres_table,
                    postgres_id=postgres_id,
                    chunk_id=chunk_id,
                    qdrant_collection=qdrant_collection,
                    qdrant_point_id=qdrant_point_id,
                )
            existing.content_hash = content_hash
            existing.embedding_model = embedding_model
            existing.reranker_model = reranker_model
            existing.index_status = "indexed"
            existing.last_indexed_at = datetime.now(UTC)
            session.add(existing)
            session.flush()
            session.refresh(existing)
            return existing

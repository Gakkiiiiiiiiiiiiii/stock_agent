from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from storage.db import session_scope
from storage.models.vector import MemoryEvidence, MemoryRecord, VectorIndexMapping, VectorIndexTask


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

    def get_by_merge_key(self, merge_key: str) -> MemoryRecord | None:
        with session_scope() as session:
            return session.execute(select(MemoryRecord).where(MemoryRecord.merge_key == merge_key)).scalars().first()

    def next_version(self, memory_id: int) -> int:
        from storage.models.research import MemoryVersion

        with session_scope() as session:
            versions = session.execute(select(MemoryVersion.version).where(MemoryVersion.memory_id == memory_id)).scalars().all()
            return max(versions, default=0) + 1

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

    def add_evidence(
        self,
        memory_id: int,
        decision_id: str | None = None,
        regime: str | None = None,
        horizon_days: int | None = None,
        market_excess_return: float | None = None,
        sector_excess_return: float | None = None,
        decision_quality: float | None = None,
        applicability: float | None = None,
        weight: float = 1.0,
        created_at: datetime | None = None,
    ) -> MemoryEvidence:
        """Persist one outcome-evidence event for a memory.

        Upsert semantics: when ``decision_id`` is given, at most one row exists per
        (memory_id, decision_id, horizon_days) — a repeat call refreshes that row.
        Anonymous events (``decision_id=None``) are always appended, because legacy
        callers record repeated outcomes without a decision identity.
        """
        with session_scope() as session:
            evidence = None
            if decision_id is not None:
                horizon_clause = (
                    MemoryEvidence.horizon_days == horizon_days
                    if horizon_days is not None
                    else MemoryEvidence.horizon_days.is_(None)
                )
                evidence = session.execute(
                    select(MemoryEvidence).where(
                        MemoryEvidence.memory_id == memory_id,
                        MemoryEvidence.decision_id == decision_id,
                        horizon_clause,
                    )
                ).scalars().first()
            if evidence is None:
                evidence = MemoryEvidence(memory_id=memory_id, decision_id=decision_id)
                session.add(evidence)
            evidence.regime = regime
            evidence.horizon_days = horizon_days
            evidence.market_excess_return = market_excess_return
            evidence.sector_excess_return = sector_excess_return
            evidence.decision_quality = decision_quality
            evidence.applicability = applicability
            evidence.weight = weight
            evidence.created_at = created_at or datetime.now(UTC)
            session.flush()
            session.refresh(evidence)
            return evidence

    def list_evidence(self, memory_id: int) -> list[MemoryEvidence]:
        with session_scope() as session:
            return list(
                session.execute(
                    select(MemoryEvidence).where(MemoryEvidence.memory_id == memory_id).order_by(MemoryEvidence.created_at.asc(), MemoryEvidence.id.asc())
                ).scalars()
            )

    def latest_evidence_summary(self, memory_id: int) -> dict:
        events = self.list_evidence(memory_id)
        excess_values = [float(item.market_excess_return) for item in events if item.market_excess_return is not None]
        quality_values = [float(item.decision_quality) for item in events if item.decision_quality is not None]
        return {
            "memory_id": memory_id,
            "evidence_count": len(events),
            "last_evidence_at": events[-1].created_at if events else None,
            "avg_market_excess_return": (sum(excess_values) / len(excess_values)) if excess_values else None,
            "avg_decision_quality": (sum(quality_values) / len(quality_values)) if quality_values else None,
        }


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

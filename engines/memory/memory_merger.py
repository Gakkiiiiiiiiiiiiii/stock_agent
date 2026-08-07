from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from engines.memory.models import MemoryCandidate
from engines.memory.conflict_resolver import MemoryConflictResolver
from engines.memory.memory_writer import enqueue_memory_reindex, write_memory_and_enqueue
from storage.db import session_scope
from storage.models.research import MemoryVersion
from storage.repositories.vector_repository import MemoryRepository


class MemoryMerger:
    def __init__(self, repository: MemoryRepository | None = None, conflict_resolver: MemoryConflictResolver | None = None) -> None:
        self.repository = repository or MemoryRepository()
        self.conflict_resolver = conflict_resolver or MemoryConflictResolver()

    def merge(self, candidate: MemoryCandidate, source_type: str, source_id: str, metadata: dict | None = None) -> dict:
        existing = self.repository.get_by_merge_key(candidate.merge_key)
        payload = self._payload(candidate, source_type, metadata)
        if existing is None:
            saved = write_memory_and_enqueue(payload, target_collection="financial_memory")
            return {"action": "created", **saved, "merge_key": candidate.merge_key}
        self._snapshot(existing.id, existing.content, existing.facts or {}, float(existing.confidence), "merge")
        conflict_type, resolution = self.conflict_resolver.resolve(existing, candidate, metadata)
        if conflict_type and resolution == "retain_existing":
            self.repository.update(existing.id, last_seen_at=datetime.now(UTC))
            return {"action": "retained_existing", "memory_id": existing.id, "conflict_type": conflict_type}
        if conflict_type:
            group = existing.conflict_group or str(uuid4())
            self.repository.update(existing.id, status="SUPERSEDED", conflict_group=group, last_seen_at=datetime.now(UTC))
            payload["merge_key"] = f"{candidate.merge_key}::{source_id}"
            payload["conflict_group"] = group
            payload["status"] = "ACTIVE"
            saved = write_memory_and_enqueue(payload, target_collection="financial_memory")
            return {"action": "conflict", "conflict_type": conflict_type, "superseded_memory_id": existing.id, "conflict_group": group, **saved}
        merged_lessons = list(dict.fromkeys([*(existing.lessons or []), *candidate.lessons]))
        saved = write_memory_and_enqueue(
            payload | {"lessons": merged_lessons, "confidence": max(float(existing.confidence), candidate.confidence)},
            target_collection="financial_memory",
            existing_memory_id=existing.id,
        )
        return {"action": "updated", **saved, "merge_key": candidate.merge_key}

    @staticmethod
    def _payload(candidate: MemoryCandidate, source_type: str, metadata: dict | None) -> dict:
        return {
            "memory_type": candidate.memory_type,
            "subject_key": candidate.subject_key,
            "merge_key": candidate.merge_key,
            "title": candidate.subject_key,
            "content": candidate.summary,
            "source_type": source_type,
            "source_date": datetime.now(UTC),
            "status": "ACTIVE",
            "importance": candidate.importance_label,
            "confidence": candidate.confidence,
            "temporal_class": candidate.temporal_class,
            "facts": candidate.facts,
            "lessons": candidate.lessons,
            "metadata_json": metadata or {},
            "valid_from": candidate.valid_from,
            "valid_to": candidate.valid_to,
            "last_seen_at": datetime.now(UTC),
        }

    @staticmethod
    def _snapshot(memory_id: int, content: str, facts: dict, confidence: float, reason: str) -> None:
        with session_scope() as session:
            count = session.query(MemoryVersion).filter(MemoryVersion.memory_id == memory_id).count()
            session.add(MemoryVersion(memory_id=memory_id, version=count + 1, content=content, facts=facts, confidence=confidence, change_reason=reason))

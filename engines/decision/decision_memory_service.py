"""D7 memory admission policy."""
from __future__ import annotations

from datetime import datetime
from contracts.decision_memory import DecisionMemory, DecisionMemoryCandidate
from storage.repositories.research_repository import DecisionMemoryRepository


class DecisionMemoryService:
    def __init__(self, repository: DecisionMemoryRepository | None = None, *, clock=None) -> None:
        self.repository = repository or DecisionMemoryRepository()
        self.clock = clock

    def admit(self, candidate: DecisionMemoryCandidate, *, decision_id: str, review_id: str, created_at: datetime | None = None) -> DecisionMemory:
        return self.repository.build_from_candidate(candidate, decision_id=decision_id, review_id=review_id, created_at=created_at)

    def save(self, memory: DecisionMemory) -> DecisionMemory:
        row = self.repository.save(memory)
        return DecisionMemory.model_validate(row.payload_json)

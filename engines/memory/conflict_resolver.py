from __future__ import annotations

from enum import StrEnum

from engines.memory.models import MemoryCandidate


class MemoryConflictType(StrEnum):
    VALUE_CONFLICT = "VALUE_CONFLICT"
    STANCE_CONFLICT = "STANCE_CONFLICT"
    TEMPORAL_UPDATE = "TEMPORAL_UPDATE"
    OUTCOME_INVALIDATION = "OUTCOME_INVALIDATION"
    SOURCE_DISAGREEMENT = "SOURCE_DISAGREEMENT"


class MemoryConflictResolver:
    conflicting_keys = {"stance", "target_price", "valuation", "expected_growth", "catalyst_status", "risk_status", "strategy_effectiveness", "market_regime_applicability"}

    def resolve(self, existing, candidate: MemoryCandidate, source_metadata: dict | None = None) -> tuple[MemoryConflictType | None, str]:
        old_facts, new_facts = existing.facts or {}, candidate.facts or {}
        conflicts = [key for key in self.conflicting_keys if key in old_facts and key in new_facts and old_facts[key] != new_facts[key]]
        if not conflicts:
            return None, "merge"
        old_validated = str(existing.status).upper() == "VALIDATED"
        old_outcome = float((existing.metadata_json or {}).get("outcome_relevance", 0))
        new_quality = float((source_metadata or {}).get("source_quality", 0.5))
        if old_validated and old_outcome >= 0.7 and new_quality < 0.7:
            return MemoryConflictType.SOURCE_DISAGREEMENT, "retain_existing"
        if candidate.temporal_class in {"TIME_SENSITIVE", "EVENT_BOUND"}:
            return MemoryConflictType.TEMPORAL_UPDATE, "supersede"
        if "stance" in conflicts:
            return MemoryConflictType.STANCE_CONFLICT, "supersede"
        return MemoryConflictType.VALUE_CONFLICT, "supersede"

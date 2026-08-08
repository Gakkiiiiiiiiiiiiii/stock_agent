from __future__ import annotations

from datetime import UTC, datetime
import warnings

from engines.memory.models import MemoryCandidate, MemoryExtractionInput


class MemoryExtractor:
    """Rule-first extractor; an LLM extractor can provide the same candidate schema later."""

    def extract(self, value: MemoryExtractionInput) -> list[MemoryCandidate]:
        metadata = value.metadata
        memory_type = str(metadata.get("memory_type") or self._infer_type(value)).upper()
        subject = str(metadata.get("subject_key") or metadata.get("subject") or metadata.get("theme") or metadata.get("strategy") or value.source_id)
        facts = dict(metadata.get("facts") or {})
        lessons = list(metadata.get("lessons") or [])
        if "lesson" in metadata:
            lessons.append(str(metadata["lesson"]))
        confidence = self._clamp(float(metadata.get("confidence", 0.72)))
        importance = self._importance(metadata, confidence, bool(lessons))
        temporal_class = str(metadata.get("temporal_class") or self._temporal_class(memory_type))
        predicate = str(metadata.get("predicate_key") or facts.get("stance") or facts.get("decision") or "summary")
        return [MemoryCandidate(
            memory_type=memory_type,
            subject_key=subject,
            summary=value.text.strip(),
            facts=facts,
            lessons=list(dict.fromkeys(lessons)),
            confidence=confidence,
            importance=importance,
            temporal_class=temporal_class,
            valid_from=metadata.get("valid_from") or datetime.now(UTC),
            valid_to=metadata.get("valid_to"),
            merge_key=f"{memory_type}::{subject}::{predicate}",
        )]

    @staticmethod
    def _infer_type(value: MemoryExtractionInput) -> str:
        source = value.source_type.lower()
        if "preference" in source:
            return "USER_PREFERENCE"
        if "regime" in source:
            return "MARKET_REGIME"
        if "decision" in source:
            return "DECISION"
        if "review" in source or "strategy" in source:
            return "STRATEGY_EXPERIENCE"
        return "THEME" if "theme" in source else "STRATEGY_EXPERIENCE"

    @staticmethod
    def _temporal_class(memory_type: str) -> str:
        return {"USER_PREFERENCE": "SLOW_CHANGING", "MARKET_REGIME": "TIME_SENSITIVE", "DECISION": "EVENT_BOUND"}.get(memory_type, "SLOW_CHANGING")

    @staticmethod
    def _importance(metadata: dict, confidence: float, has_lessons: bool) -> float:
        if "importance" in metadata:
            value = metadata["importance"]
            if isinstance(value, str):
                return {"low": 0.25, "medium": 0.5, "high": 0.85}.get(value.lower(), 0.5)
            return MemoryExtractor._clamp(float(value))
        impact = float(metadata.get("decision_impact", 0.5))
        recurrence = float(metadata.get("recurrence_probability", 0.5))
        novelty = float(metadata.get("novelty", 0.5))
        relevance = float(metadata.get("user_relevance", 0.5))
        return MemoryExtractor._clamp(0.30 * impact + 0.20 * confidence + 0.20 * recurrence + 0.15 * novelty + 0.15 * relevance + (0.08 if has_lessons else 0))

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, value))


def extract_memory(title: str, content: str, memory_type: str = "strategy_experience_memory") -> dict:
    """Compatibility adapter for existing knowledge ingestion callers."""
    warnings.warn("extract_memory is deprecated; use MemoryService.ingest instead", DeprecationWarning, stacklevel=2)
    return {
        "memory_type": memory_type,
        "title": title,
        "content": content.strip(),
        "confidence": 0.72,
        "importance": "medium",
        "status": "validated",
    }

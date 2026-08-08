from __future__ import annotations

import json

from app.model_providers import AnalysisModelClient
from engines.memory.memory_extractor import MemoryExtractor
from engines.memory.models import MemoryCandidate, MemoryExtractionInput


class LLMMemoryExtractor:
    """Structured LLM enrichment with deterministic metadata retained by rules."""

    def __init__(self, client: AnalysisModelClient | None = None, fallback: MemoryExtractor | None = None) -> None:
        self.client = client or AnalysisModelClient()
        self.fallback = fallback or MemoryExtractor()

    def available(self) -> bool:
        return self.client.available()

    def extract(self, value: MemoryExtractionInput) -> list[MemoryCandidate]:
        fallback = self.fallback.extract(value)
        if not self.available():
            return fallback
        response = self.client.complete(
            prompt=value.text,
            system=("Extract reusable investment memories. Return one JSON object with memory_type, subject_key, summary, facts, lessons, confidence, importance, temporal_class, predicate_key. "
                    "Do not invent source facts; use only the supplied text."),
            response_format={"type": "json_object"},
        )
        try:
            payload = self._parse(response.get("content") or "")
            base = fallback[0]
            facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else base.facts
            predicate = str(payload.get("predicate_key") or facts.get("stance") or facts.get("decision") or "summary")
            return [MemoryCandidate(
                memory_type=str(payload.get("memory_type") or base.memory_type).upper(),
                subject_key=str(payload.get("subject_key") or base.subject_key),
                summary=str(payload.get("summary") or base.summary),
                facts=facts,
                lessons=[str(item) for item in (payload.get("lessons") or base.lessons)],
                confidence=max(0.0, min(1.0, float(payload.get("confidence", base.confidence)))),
                importance=max(0.0, min(1.0, float(payload.get("importance", base.importance)))),
                temporal_class=str(payload.get("temporal_class") or base.temporal_class),
                valid_from=base.valid_from,
                valid_to=base.valid_to,
                merge_key=f"{str(payload.get('memory_type') or base.memory_type).upper()}::{str(payload.get('subject_key') or base.subject_key)}::{predicate}",
            )]
        except Exception:
            return fallback

    @staticmethod
    def _parse(content: str) -> dict:
        content = content.strip()
        if content.startswith("```"):
            content = "\n".join(content.splitlines()[1:-1])
        return json.loads(content[content.find("{") : content.rfind("}") + 1])

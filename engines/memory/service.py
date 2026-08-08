from __future__ import annotations

from engines.memory.memory_extractor import MemoryExtractor
from engines.memory.llm_memory_extractor import LLMMemoryExtractor
from engines.memory.memory_merger import MemoryMerger
from engines.memory.models import MemoryExtractionInput
from storage.bootstrap import create_all


class MemoryService:
    def __init__(self, extractor: MemoryExtractor | None = None, merger: MemoryMerger | None = None, llm_extractor: LLMMemoryExtractor | None = None) -> None:
        self.extractor = extractor or MemoryExtractor()
        self.merger = merger or MemoryMerger()
        self.llm_extractor = llm_extractor or LLMMemoryExtractor(fallback=self.extractor)

    def ingest(self, source_type: str, source_id: str, text: str, metadata: dict | None = None) -> list[dict]:
        create_all()
        value = MemoryExtractionInput(source_type=source_type, source_id=source_id, text=text, metadata=metadata or {})
        candidates = self.llm_extractor.extract(value) if value.metadata.get("use_llm", source_type in {"decision_review", "research_report"}) else self.extractor.extract(value)
        return [self.merger.merge(candidate, source_type, source_id, value.metadata) for candidate in candidates]

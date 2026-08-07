from __future__ import annotations

from engines.memory.memory_extractor import MemoryExtractor
from engines.memory.memory_merger import MemoryMerger
from engines.memory.models import MemoryExtractionInput
from storage.bootstrap import create_all


class MemoryService:
    def __init__(self, extractor: MemoryExtractor | None = None, merger: MemoryMerger | None = None) -> None:
        self.extractor = extractor or MemoryExtractor()
        self.merger = merger or MemoryMerger()

    def ingest(self, source_type: str, source_id: str, text: str, metadata: dict | None = None) -> list[dict]:
        create_all()
        value = MemoryExtractionInput(source_type=source_type, source_id=source_id, text=text, metadata=metadata or {})
        return [self.merger.merge(candidate, source_type, source_id, value.metadata) for candidate in self.extractor.extract(value)]

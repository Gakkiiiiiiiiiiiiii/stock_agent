from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MemoryExtractionInput(BaseModel):
    source_type: str
    source_id: str
    text: str
    metadata: dict = Field(default_factory=dict)


class MemoryCandidate(BaseModel):
    memory_type: str
    subject_key: str
    summary: str
    facts: dict = Field(default_factory=dict)
    lessons: list[str] = Field(default_factory=list)
    confidence: float = 0.7
    importance: float = 0.5
    temporal_class: str = "SLOW_CHANGING"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    merge_key: str

    @property
    def importance_label(self) -> str:
        if self.importance >= 0.75:
            return "high"
        if self.importance <= 0.35:
            return "low"
        return "medium"

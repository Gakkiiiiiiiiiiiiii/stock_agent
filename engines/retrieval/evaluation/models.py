from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievalGoldenCase(BaseModel):
    id: str
    query: str
    task_type: str | None = None
    expected_subjects: list[str] = Field(default_factory=list)
    expected_sources: list[dict] = Field(default_factory=list)
    must_not_return: list[str] = Field(default_factory=list)

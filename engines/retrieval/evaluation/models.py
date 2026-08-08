from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field


class RetrievalGoldenCase(BaseModel):
    case_id: str = Field(validation_alias=AliasChoices("case_id", "id"))
    query: str
    task_type: str | None = None
    expected_ids: list[str] = Field(default_factory=list)
    expected_subjects: list[str] = Field(default_factory=list)
    expected_memory_types: list[str] = Field(default_factory=list)
    expected_regime: str | None = None
    expected_sources: list[dict] = Field(default_factory=list)
    forbidden_ids: list[str] = Field(default_factory=list, validation_alias=AliasChoices("forbidden_ids", "must_not_return"))
    as_of: datetime | None = None
    tags: list[str] = Field(default_factory=list)


class AblationResult(BaseModel):
    variant: str
    recall_at_5: float
    recall_at_10: float
    precision_at_5: float
    mrr: float
    ndcg_at_10: float
    temporal_accuracy: float
    conflict_accuracy: float
    memory_type_recall: float
    regime_conditioned_recall: float
    expired_leakage: float
    source_priority_accuracy: float
    avg_latency_ms: float

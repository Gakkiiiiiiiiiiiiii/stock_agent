from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field, field_validator

# Dataset schema v2: query categories (see datasets/README.md).
QUERY_CATEGORIES = (
    "当前市场方向",
    "历史主题逻辑",
    "个股研究",
    "决策经验",
    "用户偏好",
    "视频最新观点",
    "冲突知识",
    "已过期知识",
)

# Graded relevance labels per (query, doc). Numeric grades feed nDCG gains;
# recall/MRR count every doc with gain >= 1 (partially_relevant included).
GRADE_GAIN = {
    "relevant": 2.0,
    "highly_relevant": 3.0,
    "partially_relevant": 1.0,
    "irrelevant": 0.0,
}
SPECIAL_LABELS = ("expired", "contradictory")


class ContradictionPair(BaseModel):
    """A contradictory doc pair: winner_id is canonical, loser_id must rank below or be absent."""

    winner_id: str
    loser_id: str


class RetrievalGoldenCase(BaseModel):
    case_id: str = Field(validation_alias=AliasChoices("case_id", "id"))
    query: str
    task_type: str | None = None
    retrieval_filters: dict = Field(default_factory=dict)
    expected_ids: list[str] = Field(default_factory=list)
    expected_subjects: list[str] = Field(default_factory=list)
    expected_memory_types: list[str] = Field(default_factory=list)
    expected_regime: str | None = None
    expected_sources: list[dict] = Field(default_factory=list)
    forbidden_ids: list[str] = Field(default_factory=list, validation_alias=AliasChoices("forbidden_ids", "must_not_return"))
    as_of: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    # --- schema v2 (optional; v1 binary labels keep working unchanged) ---
    category: str | None = None
    graded_labels: dict[str, str | int] = Field(default_factory=dict)
    superseded: dict[str, str] = Field(default_factory=dict)  # stale doc -> fresher valid replacement
    contradictions: list[ContradictionPair] = Field(default_factory=list)

    @field_validator("category")
    @classmethod
    def _category_known(cls, value: str | None) -> str | None:
        if value is not None and value not in QUERY_CATEGORIES:
            raise ValueError(f"unknown category: {value}")
        return value

    @field_validator("graded_labels")
    @classmethod
    def _labels_known(cls, value: dict[str, str | int]) -> dict[str, str | int]:
        for label in value.values():
            if isinstance(label, int):
                if label not in (0, 1, 2, 3):
                    raise ValueError(f"unknown grade: {label}")
            elif label not in GRADE_GAIN and label not in SPECIAL_LABELS:
                raise ValueError(f"unknown grade label: {label}")
        return value

    def gain_map(self) -> dict[str, float]:
        """Numeric graded gains (grade >= 1 only). v1 expected_ids map to grade 2."""
        if self.graded_labels:
            gains: dict[str, float] = {}
            for doc_id, label in self.graded_labels.items():
                gain = float(label) if isinstance(label, int) else GRADE_GAIN.get(label, 0.0)
                if gain >= 1:
                    gains[doc_id] = gain
            return gains
        return {doc_id: 2.0 for doc_id in (self.expected_ids or self.expected_subjects)}

    def relevant_ids(self) -> set[str]:
        return set(self.gain_map())

    def expired_ids(self) -> set[str]:
        return {doc_id for doc_id, label in self.graded_labels.items() if label == "expired"}

    def contradictory_ids(self) -> set[str]:
        labeled = {doc_id for doc_id, label in self.graded_labels.items() if label == "contradictory"}
        return labeled | {pair.loser_id for pair in self.contradictions}


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
    # --- schema v2 metrics (defaulted so v1 summaries still validate) ---
    temporal_precision: float = 1.0
    expired_context_rate: float = 0.0
    conflict_resolution_accuracy: float = 1.0
    source_diversity: float = 1.0

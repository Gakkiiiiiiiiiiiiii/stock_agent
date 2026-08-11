"""机会候选与排序结果的 pydantic 模型（设计文档 §11.2 输出形状）。"""
from __future__ import annotations

from pydantic import BaseModel, Field

#: 固定的成分顺序，保证 evidence_refs / components 序列化结果确定性。
COMPONENT_ORDER: tuple[str, ...] = ("theme", "technical", "alpha", "regime_fit", "knowledge", "risk")


class OpportunityCandidate(BaseModel):
    """机会排序的输入候选。

    各成分分数均为 0-100 区间；缺失的成分在评分时按中性分处理并记录 note。
    """

    symbol: str
    theme: str | None = None
    sector: str | None = None
    technical_score: float | None = None
    factor_score: float | None = None
    theme_score: float | None = None
    regime_fit_score: float | None = None
    liquidity_score: float | None = None
    risk_score: float | None = None
    knowledge_score: float | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    trigger_conditions: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)


class RankedOpportunity(BaseModel):
    """排序输出（设计文档 §11.2 JSON 形状）。"""

    rank: int
    symbol: str
    opportunity_score: float
    confidence: float = Field(ge=0.0, le=1.0)
    components: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    trigger_conditions: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)

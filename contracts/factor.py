"""Versioned, implementation-neutral contracts for the Factor service."""
from __future__ import annotations

from pydantic import BaseModel, Field


FACTOR_API_VERSION = "factor.v1"


class MiningJobRequest(BaseModel):
    rounds: int | None = Field(default=None, ge=1)
    candidates_per_round: int | None = Field(default=None, ge=1)
    symbols: list[str] = Field(default_factory=list)
    days: int | None = Field(default=None, ge=60)
    eval_window: int | None = Field(default=None, ge=1)
    horizon: int = Field(default=5, ge=1, le=60)
    candidates: list[dict] = Field(default_factory=list)
    use_model: bool = False


class MiningJobResponse(BaseModel):
    job_id: str
    status: str


class AlphaScoreRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    as_of: str | None = None
    # 设计文档 §14.2：可选因子集（当前 factor 服务仅支持 production 全集）
    factor_set: str | None = "production"


class AlphaScoreEvidence(BaseModel):
    factor_id: str
    contribution: float | None = None


class AlphaScore(BaseModel):
    """§14.2 On-demand Alpha Score 单项。"""

    symbol: str
    score: float | None = None
    rank: int | None = None
    evidence: list[AlphaScoreEvidence] = Field(default_factory=list)


class AlphaScoreResponse(BaseModel):
    as_of: str | None = None
    factor_set_version: str | None = None
    market_snapshot_id: str | None = None
    data_version: str | None = None
    data_snapshot_id: str | None = None
    scores: list[AlphaScore] = Field(default_factory=list)
    # 兼容旧结构的精简条目 {symbol, score}
    items: list[dict] = Field(default_factory=list)

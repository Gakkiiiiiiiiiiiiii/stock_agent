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


class MiningJobResponse(BaseModel):
    job_id: str
    status: str


class AlphaScoreRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    as_of: str | None = None


class AlphaScore(BaseModel):
    symbol: str
    alpha_score: float
    alpha_rank: int
    factor_count: int


class AlphaScoreResponse(BaseModel):
    as_of: str | None = None
    factor_version: str | None = None
    data_version: str | None = None
    items: list[AlphaScore] = Field(default_factory=list)

"""Versioned, implementation-neutral contracts for the Content service."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CONTENT_API_VERSION = "content.v1"
# P0 A-04：main 主契约为 content-factor-signal.v3；v2 仅作为显式 legacy
# compatibility（只服务旧 Release lane），main 不得默认 v2。
CONTENT_FACTOR_SIGNAL_VERSION = "content-factor-signal.v3"
CONTENT_FACTOR_SIGNAL_LEGACY_VERSION = "content-factor-signal.v2"


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    filters: dict[str, object] = Field(default_factory=dict)
    limit: int = Field(default=20, ge=1, le=100)
    intent: str | None = None


class ContentSignalRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    start: str
    end: str
    minimum_support_status: str = "SOURCE_SUPPORTED"
    # 默认 v3；显式传 v2 只允许在 legacy mode（旧 Release lane）。
    contract_version: str = CONTENT_FACTOR_SIGNAL_VERSION


class ContentSignal(BaseModel):
    knowledge_uid: str
    ticker: str | None = None
    subject_key: str
    source_video_id: int | str | None = None
    as_of_time: datetime | str
    available_from: str
    knowledge_kind: str
    sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"] | str = "NEUTRAL"
    truth_status: str
    support_status: str
    review_status: str
    evidence_ids: list[str] = Field(default_factory=list)
    content_attention_score: float = 0.0
    cross_video_consensus: float = 0.0
    cross_video_disagreement: float = 0.0
    # ---- v3 lineage（P0 A-04：不得丢失）----
    signal_id: str | None = None
    signal_schema_version: str = CONTENT_FACTOR_SIGNAL_VERSION
    producer_version: str | None = None
    signal_status: str | None = None
    content_snapshot_id: str | None = None
    claim_id: str | None = None
    event_time: datetime | str | None = None
    published_at: str | None = None
    signal_type: str | None = None
    direction: str | None = None
    magnitude: float | None = None
    confidence: float | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    market_snapshot_id: str | None = None
    market_data_version: str | None = None
    producer: dict[str, object] = Field(default_factory=dict)


class ContentSignalResponse(BaseModel):
    contract_version: Literal["content-factor-signal.v3"] = (
        CONTENT_FACTOR_SIGNAL_VERSION
    )
    items: list[ContentSignal] = Field(default_factory=list)


class ContentSignalLegacy(BaseModel):
    """v2 legacy 模型：只服务旧 Release lane，main 默认禁止使用。"""

    knowledge_uid: str
    ticker: str | None = None
    subject_key: str
    source_video_id: int | str | None = None
    as_of_time: datetime | str
    available_from: str
    knowledge_kind: str
    sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"] | str = "NEUTRAL"
    truth_status: str
    support_status: str
    review_status: str
    evidence_ids: list[str] = Field(default_factory=list)
    content_attention_score: float = 0.0
    cross_video_consensus: float = 0.0
    cross_video_disagreement: float = 0.0


class ContentSignalLegacyResponse(BaseModel):
    contract_version: Literal["content-factor-signal.v2"] = (
        CONTENT_FACTOR_SIGNAL_LEGACY_VERSION
    )
    items: list[ContentSignalLegacy] = Field(default_factory=list)

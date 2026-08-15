"""Versioned, implementation-neutral contracts for the Content service."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


CONTENT_API_VERSION = "content.v1"
CONTENT_FACTOR_SIGNAL_VERSION = "content-factor-signal.v2"


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


class ContentSignalResponse(BaseModel):
    contract_version: Literal["content-factor-signal.v2"] = CONTENT_FACTOR_SIGNAL_VERSION
    items: list[ContentSignal] = Field(default_factory=list)

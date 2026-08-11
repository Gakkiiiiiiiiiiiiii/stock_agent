from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class StrategyStatus(StrEnum):
    IDEA = "IDEA"
    GENERATED = "GENERATED"
    STATIC_VALIDATED = "STATIC_VALIDATED"
    BACKTESTED = "BACKTESTED"
    OOS_VALIDATED = "OOS_VALIDATED"
    PAPER_TRACKING = "PAPER_TRACKING"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    DECAYING = "DECAYING"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class StrategyDefinition(BaseModel):
    strategy_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    universe: dict
    entry_rules: list[dict]
    exit_rules: list[dict]
    ranking: list[dict] = Field(default_factory=list)
    portfolio: dict = Field(default_factory=dict)
    execution: dict = Field(default_factory=lambda: {"signal_at": "close", "fill_at": "next_open"})
    status: StrategyStatus = StrategyStatus.IDEA
    version: int = 1


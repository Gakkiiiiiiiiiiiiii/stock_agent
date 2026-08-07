from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AgentRuntimeContext(BaseModel):
    query: str
    as_of: datetime
    user_preferences: dict = Field(default_factory=dict)
    current_positions: list[dict] = Field(default_factory=list)
    market_regime: dict | None = None
    strategy_memories: list[dict] = Field(default_factory=list)
    decision_memories: list[dict] = Field(default_factory=list)
    knowledge_contexts: list[dict] = Field(default_factory=list)
    task_context: dict = Field(default_factory=dict)

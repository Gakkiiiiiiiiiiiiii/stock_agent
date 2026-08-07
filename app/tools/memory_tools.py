from __future__ import annotations

from pydantic import BaseModel, Field

from app.tools.definitions import ToolDefinition
from mcp_servers import retrieval_server


class SearchMemoryInput(BaseModel):
    query: str
    memory_types: list[str] = Field(default_factory=list)
    market_regime: str | None = None
    top_k: int = 5


def build_memory_tools() -> list[ToolDefinition]:
    def search(payload: dict) -> dict:
        return retrieval_server.search_memory(**payload)

    return [
        ToolDefinition(name="search_memory", description="Search long-term memory records and rank by relevance, importance, confidence, recency, regime, and outcome.", input_model=SearchMemoryInput, executor=search, category="memory"),
        ToolDefinition(name="search_strategy_memory", description="Search strategy experience memories only.", input_model=SearchMemoryInput, executor=lambda payload: search(payload | {"memory_types": ["STRATEGY_EXPERIENCE"]}), category="memory"),
        ToolDefinition(name="search_decision_memory", description="Search prior decision memories only.", input_model=SearchMemoryInput, executor=lambda payload: search(payload | {"memory_types": ["DECISION"]}), category="memory"),
        ToolDefinition(name="search_user_preferences", description="Search stable user preference memories only.", input_model=SearchMemoryInput, executor=lambda payload: search(payload | {"memory_types": ["USER_PREFERENCE"]}), category="memory"),
    ]

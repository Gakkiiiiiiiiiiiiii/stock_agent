from __future__ import annotations

from pydantic import BaseModel, Field

from app.tools.definitions import ToolDefinition
from storage.repositories.research_repository import DecisionMemoryRepository


class SearchMemoryInput(BaseModel):
    query: str
    memory_types: list[str] = Field(default_factory=list)
    market_regime: str | None = None
    top_k: int = 5


def build_memory_tools() -> list[ToolDefinition]:
    def search(payload: dict) -> dict:
        query = str(payload.get("query", "")).lower()
        types = set(payload.get("memory_types") or [])
        rows = DecisionMemoryRepository().list_active(limit=int(payload.get("top_k", 5)))
        items = []
        for memory in rows:
            if types and memory.memory_type not in types:
                continue
            if query and query not in memory.content.lower():
                continue
            evidence = memory.to_evidence()
            items.append({"memory_id": memory.memory_id, "content": memory.content, "memory_type": memory.memory_type, "weight": min(memory.weight, 0.25), "source_system": "stock_agent", "evidence_type": "DECISION_MEMORY", "evidence": evidence.model_dump(mode="json")})
        return {"items": items, "source_system": "stock_agent", "evidence_type": "DECISION_MEMORY"}

    return [
        ToolDefinition(name="search_decision_memory", description="Search stock_agent-owned decision memories only.", input_model=SearchMemoryInput, executor=lambda payload: search(payload | {"memory_types": ["DECISION_CASE", "FAILURE_PATTERN", "SUCCESS_PATTERN", "REGIME_EXPERIENCE", "RISK_MISS", "POLICY_ADJUSTMENT"]}), category="memory"),
        ToolDefinition(name="search_user_preferences", description="Search stable user preference memories only.", input_model=SearchMemoryInput, executor=lambda payload: search(payload | {"memory_types": ["USER_PREFERENCE"]}), category="memory"),
    ]

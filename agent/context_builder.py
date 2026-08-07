from __future__ import annotations

from datetime import UTC, datetime

from agent.runtime_context import AgentRuntimeContext
from engines.memory.memory_scorer import MemoryScorer
from storage.repositories.research_repository import MarketRegimeRepository
from storage.repositories.vector_repository import MemoryRepository


class ContextBuilder:
    """Builds stable agent context without requiring the LLM to rediscover stored state."""

    def __init__(self, memory_repository: MemoryRepository | None = None, regime_repository: MarketRegimeRepository | None = None) -> None:
        self.memory_repository = memory_repository or MemoryRepository()
        self.regime_repository = regime_repository or MarketRegimeRepository()

    def build(self, query: str, provided: dict | None = None, market_code: str = "CN_A") -> dict:
        provided = provided or {}
        try:
            regime_state = self.regime_repository.get_state(market_code)
            regime = None if regime_state is None else {"market_code": market_code, "confirmed_regime": regime_state.confirmed_regime, "confidence": regime_state.confidence, "confirmed_since": regime_state.confirmed_since.isoformat()}
            records = [item for item in self.memory_repository.list_all() if str(item.status).upper() in {"ACTIVE", "VALIDATED"} and not item.is_deleted]
        except Exception:  # Database bootstrap can legitimately precede this builder.
            regime, records = None, []
        words = {part.lower() for part in query.split() if len(part) > 1}
        memories = []
        for item in records:
            text = f"{item.title} {item.content}".lower()
            semantic = 1.0 if words and any(word in text for word in words) else 0.0
            memories.append({"record": {"id": item.id, "memory_type": item.memory_type, "content": item.content, "importance": item.importance, "confidence": item.confidence, "related_regime": item.related_regime, "source_date": item.source_date.isoformat() if item.source_date else None, "metadata_json": item.metadata_json or {}}, "final_score": semantic})
        ranked = MemoryScorer().rank(memories, regime.get("confirmed_regime") if regime else None)[:5]
        runtime = AgentRuntimeContext(
            query=query, as_of=datetime.now(UTC), market_regime=regime,
            strategy_memories=[item for item in ranked if item["record"]["memory_type"] == "STRATEGY_EXPERIENCE"],
            decision_memories=[item for item in ranked if item["record"]["memory_type"] == "DECISION"],
            user_preferences={item["record"]["content"]: item["record"] for item in ranked if item["record"]["memory_type"] == "USER_PREFERENCE"},
            current_positions=list(provided.get("current_positions") or []),
            knowledge_contexts=list(provided.get("knowledge_contexts") or []),
            task_context={key: value for key, value in provided.items() if key not in {"current_positions", "knowledge_contexts"}},
        )
        return runtime.model_dump(mode="json")

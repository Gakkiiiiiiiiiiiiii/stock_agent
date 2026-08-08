from __future__ import annotations

from datetime import UTC, datetime

from agent.runtime_context import AgentRuntimeContext
from engines.memory.memory_retriever import retrieve_memory
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
            records = []
        except Exception:  # Database bootstrap can legitimately precede this builder.
            regime, records = None, []
        try:
            strategy = retrieve_memory(query, memory_types=["STRATEGY_EXPERIENCE"], market_regime=regime.get("confirmed_regime") if regime else None, top_k=5).get("memories", [])
            decisions = retrieve_memory(query, memory_types=["DECISION"], market_regime=regime.get("confirmed_regime") if regime else None, top_k=3).get("memories", [])
            preferences = retrieve_memory(query, memory_types=["USER_PREFERENCE"], top_k=5).get("memories", [])
        except Exception:
            strategy, decisions, preferences = [], [], []
        runtime = AgentRuntimeContext(
            query=query, as_of=datetime.now(UTC), market_regime=regime,
            strategy_memories=strategy, decision_memories=decisions,
            user_preferences={str((item.get("record") or {}).get("id")): item.get("record") for item in preferences},
            current_positions=list(provided.get("current_positions") or []),
            knowledge_contexts=list(provided.get("knowledge_contexts") or []),
            task_context={key: value for key, value in provided.items() if key not in {"current_positions", "knowledge_contexts"}},
        )
        return runtime.model_dump(mode="json")

from __future__ import annotations

from engines.retrieval.hybrid_retriever import HybridRetriever
from engines.memory.memory_scorer import MemoryScorer


def retrieve_memory(query: str, filters: dict | None = None, top_k: int = 5, memory_types: list[str] | None = None, market_regime: str | None = None) -> dict:
    result = HybridRetriever().retrieve(query=query, task_type="memory_lookup", filters=filters, top_k=top_k)
    contexts = [item for item in result.get("contexts", []) if item.get("record") and (not memory_types or item["record"].get("memory_type") in memory_types)]
    result["contexts"] = MemoryScorer().rank(contexts, market_regime)[:top_k]
    result["memories"] = result["contexts"]
    return result

from __future__ import annotations

from engines.retrieval.hybrid_retriever import HybridRetriever
from engines.retrieval.filters import normalize_retrieval_filters
from engines.memory.memory_scorer import MemoryScorer


def retrieve_memory(query: str, filters: dict | None = None, top_k: int = 5, memory_types: list[str] | None = None, market_regime: str | None = None) -> dict:
    pushed_filters = normalize_retrieval_filters(filters)
    if memory_types:
        pushed_filters["memory_type"] = list(dict.fromkeys([*(pushed_filters.get("memory_type") or []), *memory_types]))
    # Type constraints are applied in both dense and sparse recall before the
    # candidate limit, so a crowded unrelated Top-N cannot hide typed memory.
    result = HybridRetriever().retrieve(query=query, task_type="memory_lookup", filters=pushed_filters or None, top_k=top_k)
    contexts = [item for item in result.get("contexts", []) if item.get("record") and (not memory_types or item["record"].get("memory_type") in memory_types)]
    result["contexts"] = MemoryScorer().rank(contexts, market_regime)[:top_k]
    result["memories"] = result["contexts"]
    return result

from __future__ import annotations


def build_retrieval_plan(query: str, task_type: str | None = None, filters: dict | None = None, top_k: int = 5) -> dict:
    normalized = query.strip()
    lowered = normalized.lower()
    inferred_task_type = task_type or ("strategy_question" if "b1" in lowered or "b2" in lowered or "b3" in lowered else "general_research")
    return {
        "task_type": inferred_task_type,
        "query": normalized,
        "filters": filters or {},
        "collections": ["financial_memory", "financial_knowledge"],
        "top_n_retrieve": max(top_k * 4, 10),
        "top_k_rerank": top_k,
    }


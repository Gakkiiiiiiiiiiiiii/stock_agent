from __future__ import annotations


def build_retrieval_plan(query: str, task_type: str | None = None, filters: dict | None = None, top_k: int = 5) -> dict:
    normalized = query.strip()
    lowered = normalized.lower()
    inferred_task_type = task_type or ("strategy_question" if "b1" in lowered or "b2" in lowered or "b3" in lowered else "general_research")
    preferred_source_types: list[str] = []
    top_n_retrieve = max(top_k * 4, 10)
    if _is_recent_market_opportunity_query(normalized):
        inferred_task_type = "market_opportunity_scan"
        preferred_source_types = [
            "bilibili_video_viewpoint",
            "bilibili_financial_event",
            "bilibili_video_summary",
        ]
        top_n_retrieve = max(top_k * 6, 18)
    return {
        "task_type": inferred_task_type,
        "query": normalized,
        "filters": filters or {},
        "collections": ["financial_memory", "financial_knowledge"],
        "top_n_retrieve": top_n_retrieve,
        "top_k_rerank": top_k,
        "preferred_source_types": preferred_source_types,
    }


def _is_recent_market_opportunity_query(query: str) -> bool:
    lowered = (query or "").strip().lower()
    if not lowered:
        return False
    recency_keywords = ("最近", "近期", "当前", "今天", "这两天", "这几天", "最新", "本周", "眼下")
    opportunity_keywords = ("板块", "赛道", "方向", "机会", "主线", "值得关注", "值得投资", "可交易", "怎么看")
    return any(keyword in lowered for keyword in recency_keywords) and any(keyword in lowered for keyword in opportunity_keywords)

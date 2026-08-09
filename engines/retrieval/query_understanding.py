from __future__ import annotations


def build_retrieval_plan(query: str, task_type: str | None = None, filters: dict | None = None, top_k: int = 5) -> dict:
    normalized = query.strip()
    lowered = normalized.lower()
    inferred_task_type = task_type or _infer_task_type(normalized)
    preferred_source_types: list[str] = []
    top_n_retrieve = max(top_k * 4, 10)
    collections = _collections_for_task(inferred_task_type)
    if _is_recent_market_opportunity_query(normalized):
        inferred_task_type = "market_opportunity_scan"
        collections = _collections_for_task(inferred_task_type)
        preferred_source_types = [
            "video_knowledge_unit",
            "bilibili_video_viewpoint",
            "bilibili_financial_event",
            "bilibili_video_summary",
        ]
        top_n_retrieve = max(top_k * 6, 18)
    return {
        "task_type": inferred_task_type,
        "query": normalized,
        "filters": filters or {},
        "collections": collections,
        "top_n_retrieve": top_n_retrieve,
        "top_k_rerank": top_k,
        "preferred_source_types": preferred_source_types,
    }


_FACTUAL_INDICATORS = ("pe", "pb", "ps", "eps", "营收", "净利润", "毛利率", "股价", "市值", "涨跌幅", "目标价", "公告", "财报", "政策", "利率", "cpi", "gdp")
_FACTUAL_QUESTION_WORDS = ("是多少", "多少", "几个", "几", "吗", "呢", "?")
_AUTHOR_VIEWPOINT_MARKERS = ("视频里怎么看", "博主怎么看", "作者认为", "主播认为", "up主", "说了什么", "怎么看")


def _is_factual_qa(query: str) -> bool:
    """Financial fact lookup: an indicator/metric plus a question word (§30).

    e.g. "宁德时代当前 PE 是多少" must be factual_qa, not current_state.
    """
    lowered = query.lower()
    has_indicator = any(token in lowered for token in _FACTUAL_INDICATORS)
    has_question = any(token in query for token in _FACTUAL_QUESTION_WORDS)
    return has_indicator and has_question


def _is_author_viewpoint(query: str) -> bool:
    """Asking what the video/author said or thinks (§30).

    When a query hits both factual indicators + question words and "怎么看",
    factual_qa takes precedence because it is checked first.
    """
    return any(token in query for token in _AUTHOR_VIEWPOINT_MARKERS)


def _infer_task_type(query: str) -> str:
    lowered = query.lower()
    if _is_factual_qa(query):
        return "factual_qa"
    if _is_author_viewpoint(query):
        return "author_viewpoint"
    if "b1" in lowered or "b2" in lowered or "b3" in lowered:
        return "strategy_question"
    if any(token in query for token in ("当前", "现在", "最新", "今天", "眼下", "还有效", "怎么看")):
        return "current_state"
    if any(token in query for token in ("历史", "之前", "复盘", "当时", "演变", "过去")):
        return "history_review"
    if any(token in query for token in ("买", "卖", "仓位", "操作", "交易", "止损", "机会", "风险")):
        return "trading_decision"
    if any(token in query for token in ("方法", "框架", "怎么判断", "如何分析", "逻辑")):
        return "method_explanation"
    return "general_research"


def _collections_for_task(task_type: str) -> list[str]:
    video_durable = "financial_video_durable_v1_bge_m3"
    video_timed = "financial_video_timed_v1_bge_m3"
    video_action = "financial_video_action_v1_bge_m3"
    memory = "financial_memory_v2_bge_m3"
    knowledge = "financial_knowledge_v2_bge_m3"
    if task_type == "method_explanation":
        return [video_durable, knowledge, memory]
    if task_type == "current_state":
        return [video_timed, video_action, video_durable, memory]
    if task_type == "history_review":
        return [video_timed, video_durable, knowledge, memory]
    if task_type in {"trading_decision", "market_opportunity_scan", "strategy_question"}:
        return [video_action, video_timed, video_durable, memory]
    return [video_timed, video_durable, video_action, knowledge, memory]


def _is_recent_market_opportunity_query(query: str) -> bool:
    lowered = (query or "").strip().lower()
    if not lowered:
        return False
    recency_keywords = ("最近", "近期", "当前", "今天", "这两天", "这几天", "最新", "本周", "眼下")
    opportunity_keywords = ("板块", "赛道", "方向", "机会", "主线", "值得关注", "值得投资", "可交易")
    return any(keyword in lowered for keyword in recency_keywords) and any(keyword in lowered for keyword in opportunity_keywords)

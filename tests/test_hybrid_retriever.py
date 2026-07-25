from __future__ import annotations

from engines.retrieval.hybrid_retriever import HybridRetriever
from engines.retrieval.query_understanding import build_retrieval_plan


class FakeQdrant:
    def search(self, collection, vector, limit, query_filter=None):
        _ = (collection, vector, limit, query_filter)
        return [type("Hit", (), {"id": "1", "payload": {"chunk_id": "chunk_1", "text": "A"}, "score": 0.8})()]


class FakeReranker:
    def rerank(self, query, candidates, top_k):
        _ = (query, candidates, top_k)
        return [
            {"payload": {"title": "旧观点", "source_timestamp": 10}, "text": "old", "rerank_score": 0.9},
            {"payload": {"title": "新观点", "source_timestamp": 20}, "text": "new", "rerank_score": 0.9},
        ]


class CaptureReranker:
    def __init__(self):
        self.candidates = []

    def rerank(self, query, candidates, top_k):
        _ = (query, top_k)
        self.candidates = candidates
        return [{"chunk_id": item["chunk_id"], "payload": item["payload"], "text": item["text"], "rerank_score": 0.8} for item in candidates]


class FakeEmbedder:
    def embed(self, query):
        _ = query
        return [0.1, 0.2]


class FakeHydrator:
    def hydrate(self, reranked_hits):
        return [
            {"title": "旧观点", "rerank_score": 0.9, "source_timestamp": 10, "dense_score": reranked_hits[0].get("dense_score")},
            {"title": "新观点", "rerank_score": 0.9, "source_timestamp": 20, "bm25_score": reranked_hits[0].get("bm25_score")},
        ]


class EmptyHydrator:
    def hydrate(self, reranked_hits):
        return reranked_hits


class FakeSparseRetriever:
    def search(self, query, collections, filters=None, limit=20):
        _ = (query, collections, filters, limit)
        return [
            {
                "chunk_id": "sparse_only",
                "text": "黄金 高股息 独立稀疏召回",
                "payload": {"chunk_id": "sparse_only", "text": "黄金 高股息 独立稀疏召回", "title": "稀疏候选"},
                "dense_score": 0.0,
                "score": 0.0,
                "sparse_recall_score": 1.0,
                "recall_sources": ["sparse"],
            }
        ]


def test_hybrid_retriever_prefers_newer_knowledge_when_scores_tie():
    retriever = HybridRetriever(
        qdrant_client=FakeQdrant(),
        reranker=FakeReranker(),
        embedder=FakeEmbedder(),
        hydrator=FakeHydrator(),
    )
    result = retriever.retrieve("半导体怎么看", top_k=2)
    assert result["contexts"][0]["title"] == "新观点"
    assert result["contexts"][1]["title"] == "旧观点"


def test_hybrid_retriever_merges_independent_sparse_candidates():
    reranker = CaptureReranker()
    retriever = HybridRetriever(
        qdrant_client=FakeQdrant(),
        reranker=reranker,
        embedder=FakeEmbedder(),
        hydrator=EmptyHydrator(),
        sparse_retriever=FakeSparseRetriever(),
    )
    retriever.retrieve("黄金 高股息", top_k=2)
    chunk_ids = {item["chunk_id"] for item in reranker.candidates}
    assert "chunk_1" in chunk_ids
    assert "sparse_only" in chunk_ids


class FakeConflictHydrator:
    def hydrate(self, reranked_hits):
        _ = reranked_hits
        return [
            {
                "title": "新看空观点",
                "rerank_score": 0.95,
                "source_timestamp": 20,
                "source_type": "bilibili_video_viewpoint",
                "related_theme": "半导体",
                "related_strategy": "viewpoint_bear",
            },
            {
                "title": "旧看多观点",
                "rerank_score": 0.95,
                "source_timestamp": 10,
                "source_type": "bilibili_video_viewpoint",
                "related_theme": "半导体",
                "related_strategy": "viewpoint_bull",
            },
            {
                "title": "同主题旧风险",
                "rerank_score": 0.94,
                "source_timestamp": 9,
                "source_type": "bilibili_video_viewpoint",
                "related_theme": "半导体",
                "related_strategy": "viewpoint_risk",
            },
            {
                "title": "无冲突操作建议",
                "rerank_score": 0.93,
                "source_timestamp": 8,
                "source_type": "bilibili_video_viewpoint",
                "related_theme": "半导体",
                "related_strategy": "viewpoint_actionable",
            },
        ]


def test_hybrid_retriever_prefers_newer_viewpoint_when_stances_conflict():
    retriever = HybridRetriever(
        qdrant_client=FakeQdrant(),
        reranker=FakeReranker(),
        embedder=FakeEmbedder(),
        hydrator=FakeConflictHydrator(),
    )
    result = retriever.retrieve("半导体怎么看", top_k=4)
    titles = [item["title"] for item in result["contexts"]]
    assert "新看空观点" in titles
    assert "旧看多观点" not in titles
    assert "同主题旧风险" in titles
    assert "无冲突操作建议" in titles


class FakeVideoPriorityHydrator:
    def hydrate(self, reranked_hits):
        _ = reranked_hits
        return [
            {
                "title": "静态主题库逻辑",
                "rerank_score": 0.9,
                "source_timestamp": 5,
                "source_type": "theme_logic",
            },
            {
                "title": "最近视频观点",
                "rerank_score": 0.9,
                "source_timestamp": 20,
                "source_type": "bilibili_video_viewpoint",
            },
        ]


def test_hybrid_retriever_prefers_recent_video_sources_for_market_opportunity_queries():
    retriever = HybridRetriever(
        qdrant_client=FakeQdrant(),
        reranker=FakeReranker(),
        embedder=FakeEmbedder(),
        hydrator=FakeVideoPriorityHydrator(),
    )
    result = retriever.retrieve("最近有什么值得关注的板块方向", top_k=2)
    assert result["contexts"][0]["title"] == "最近视频观点"
    assert result["plan"]["task_type"] == "market_opportunity_scan"
    assert result["plan"]["preferred_source_types"][0] == "bilibili_video_viewpoint"


def test_build_retrieval_plan_marks_recent_market_opportunity_queries():
    plan = build_retrieval_plan("最近有什么比较好的板块或者赛道可以进行投资", top_k=3)
    assert plan["task_type"] == "market_opportunity_scan"
    assert "bilibili_video_summary" in plan["preferred_source_types"]
    assert plan["top_n_retrieve"] >= 18


def test_reranker_candidate_fields_preserved():
    retriever = HybridRetriever(
        qdrant_client=FakeQdrant(),
        reranker=FakeReranker(),
        embedder=FakeEmbedder(),
        hydrator=FakeHydrator(),
    )
    merged = retriever._merge_candidate_fields(
        [{"chunk_id": "a", "dense_score": 0.76, "bm25_score": 0.4, "payload": {"x": 1}}],
        [{"chunk_id": "a", "rerank_score": 0.82}],
    )
    assert merged[0]["dense_score"] == 0.76
    assert merged[0]["bm25_score"] == 0.4
    assert merged[0]["rerank_score"] == 0.82


def test_source_priority_sorts_by_final_score_before_rerank_score():
    contexts = [
        {"title": "low final", "rerank_score": 0.99, "final_score": 0.2, "source_type": "theme_logic", "source_timestamp": 20},
        {"title": "high final", "rerank_score": 0.2, "final_score": 0.8, "source_type": "theme_logic", "source_timestamp": 10},
    ]
    ordered = HybridRetriever._apply_source_priority(contexts, [])
    assert ordered[0]["title"] == "high final"


def test_sparse_score_is_real():
    retriever = HybridRetriever(
        qdrant_client=FakeQdrant(),
        reranker=FakeReranker(),
        embedder=FakeEmbedder(),
        hydrator=FakeHydrator(),
    )
    scored = retriever.sparse_scorer.score_candidates("黄金 高股息", [{"chunk_id": "a", "text": "黄金 高股息 机会"}])
    assert scored[0]["bm25_score"] > 0
    assert scored[0]["sparse_score_source"] == "bm25_candidate_text"

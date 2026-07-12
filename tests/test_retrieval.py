from engines.retrieval.hybrid_retriever import HybridRetriever


class FakeQdrant:
    def search(self, collection, vector, limit, query_filter=None):
        class Hit:
            def __init__(self):
                self.id = "point-1"
                self.score = 0.9
                self.payload = {
                    "chunk_id": "memory_record_1_chunk_001",
                    "postgres_table": "memory_record",
                    "postgres_id": 1,
                    "memory_type": "strategy_experience_memory",
                    "title": "B2 在轮动行情中的失败经验",
                    "status": "validated",
                    "text": "B2 在轮动行情中容易次日兑现。",
                }

        return [Hit()]


class FakeReranker:
    def rerank(self, query, candidates, top_k=5):
        return [
            {
                "chunk_id": candidates[0]["chunk_id"],
                "rerank_score": 0.93,
                "payload": candidates[0]["payload"],
                "text": candidates[0]["text"],
            }
        ]


class FakeHydrator:
    def hydrate(self, reranked_hits):
        return [{"title": reranked_hits[0]["payload"]["title"], "rerank_score": reranked_hits[0]["rerank_score"]}]


def test_hybrid_retriever_returns_contexts():
    retriever = HybridRetriever(qdrant_client=FakeQdrant(), reranker=FakeReranker(), hydrator=FakeHydrator())
    result = retriever.retrieve("轮动行情中 B2 是否适合追", filters={"related_strategy": ["B2"]}, top_k=1)
    assert result["contexts"][0]["rerank_score"] == 0.93
    assert result["plan"]["filters"]["related_strategy"] == ["B2"]


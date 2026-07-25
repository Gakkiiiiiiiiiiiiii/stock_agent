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


def test_deployment_embedding_defaults_are_semantic():
    import yaml

    compose = yaml.safe_load(open("docker-compose.yml", encoding="utf-8"))
    api_env = compose["services"]["api"]["environment"]
    embedding_env = compose["services"]["embedding"]["environment"]
    api_depends = compose["services"]["api"]["depends_on"]
    vector_depends = compose["services"]["vector_worker"]["depends_on"]
    reranker_env = compose["services"]["reranker"]["environment"]
    assert api_env["EMBEDDING_PROVIDER"].endswith("openai_compatible}")
    assert embedding_env["EMBEDDING_PROVIDER"].endswith("sentence_transformers}")
    assert "profiles" not in compose["services"]["embedding"]
    assert api_depends["embedding"]["condition"] == "service_healthy"
    assert vector_depends["embedding"]["condition"] == "service_healthy"
    assert reranker_env["RERANKER_PROVIDER"].endswith("sentence_transformers}")


def test_embedding_api_rejects_model_override(monkeypatch):
    from workers.embedding_api import EmbeddingRequest, embeddings, get_embedder

    monkeypatch.setenv("EMBEDDING_PROVIDER", "local_ngram")
    monkeypatch.setenv("EMBEDDING_MODEL", "model-B")
    get_embedder.cache_clear()
    result = embeddings(EmbeddingRequest(input="hello", model="model-A"))
    assert result["error"]["code"] == "EMBEDDING_MODEL_MISMATCH"
    get_embedder.cache_clear()


def test_embedding_api_reuses_cached_embedder(monkeypatch):
    import workers.embedding_api as api
    from workers.embedding_api import EmbeddingRequest, embeddings, get_embedder

    calls = []

    class FakeEmbedder:
        @property
        def metadata(self):
            from engines.retrieval.embedder import EmbeddingMetadata

            return EmbeddingMetadata("fake", "model-B", 2, True)

        def embed(self, text):
            return [1.0, float(len(text))]

    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setattr(api, "build_embedder", lambda provider: calls.append(provider) or FakeEmbedder())
    get_embedder.cache_clear()
    embeddings(EmbeddingRequest(input="hello", model="model-B"))
    embeddings(EmbeddingRequest(input="world", model="model-B"))
    assert calls == ["fake"]
    get_embedder.cache_clear()


def test_collection_manifest_rejects_local_ngram_for_bge_collection():
    from engines.retrieval.collection_manifest import CollectionManifestError, validate_embedding_manifest
    from engines.retrieval.embedder import EmbeddingMetadata

    metadata = EmbeddingMetadata(provider="local_ngram", model="local-chinese-ngram-v1", dimension=1024, semantic=False)
    try:
        validate_embedding_manifest("financial_memory_v2_bge_m3", metadata)
    except CollectionManifestError as exc:
        assert "semantic" in str(exc)
    else:
        raise AssertionError("expected manifest mismatch")


def test_hydrator_preserves_hybrid_scores():
    from engines.retrieval.postgres_hydrator import PostgresHydrator

    item = {
        "payload": {"title": "测试", "source_type": "theme_logic"},
        "text": "内容",
        "dense_score": 0.7,
        "bm25_score": 0.2,
        "sparse_recall_score": 1.0,
        "rerank_score": 0.9,
        "final_score": 0.8,
    }
    hydrated = PostgresHydrator().hydrate([item])[0]
    assert hydrated["dense_score"] == 0.7
    assert hydrated["bm25_score"] == 0.2
    assert hydrated["sparse_recall_score"] == 1.0
    assert hydrated["final_score"] == 0.8

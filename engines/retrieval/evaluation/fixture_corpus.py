from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from engines.retrieval.config import RetrievalConfig
from engines.retrieval.hybrid_retriever import HybridRetriever


FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "retrieval_eval"


def load_fixture_records(path: Path = FIXTURE_ROOT / "memory_records.jsonl") -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _score(query: str, record: dict) -> float:
    haystack = " ".join(str(record.get(key) or "") for key in ("id", "text", "title", "related_theme", "related_symbol", "related_regime"))
    matches = sum(1 for token in set(query) if token in haystack)
    return matches / max(len(set(query)), 1)


class FixtureEmbedder:
    metadata = SimpleNamespace(provider="fixture", model="deterministic-character-overlap", dimension=1)

    def embed(self, text: str) -> str:
        return text


class FixtureQdrantClient:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def search(self, collection: str, vector: str, limit: int, query_filter=None):  # noqa: ANN001
        _ = collection, query_filter
        ranked = sorted(self.records, key=lambda record: (_score(vector, record), record["id"]), reverse=True)
        return [SimpleNamespace(id=record["id"], score=_score(vector, record), payload={**record, "chunk_id": record["id"]}) for record in ranked[:limit]]


class FixtureSparseRetriever:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def search(self, query: str, collections: list[str], filters: dict, limit: int) -> list[dict]:
        _ = collections, filters
        ranked = sorted(self.records, key=lambda record: (_score(query, record), record["id"]), reverse=True)
        return [{"chunk_id": record["id"], "text": record.get("text", ""), "payload": record, "sparse_recall_score": _score(query, record), "recall_sources": ["sparse"]} for record in ranked[:limit]]


class FixtureSparseScorer:
    def score_candidates(self, query: str, candidates: list[dict]) -> list[dict]:
        return [candidate | {"bm25_score": _score(query, candidate.get("payload") or {})} for candidate in candidates]


class FixtureReranker:
    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        ranked = [candidate | {"rerank_score": _score(query, candidate.get("payload") or {})} for candidate in candidates]
        return sorted(ranked, key=lambda candidate: (candidate["rerank_score"], candidate["chunk_id"]), reverse=True)[:top_k]


class FixtureHydrator:
    def hydrate(self, candidates: list[dict]) -> list[dict]:
        return [
            {
                **candidate,
                **(candidate.get("payload") or {}),
                "record": candidate.get("payload") or {},
                "source_timestamp": 1786176000,
            }
            for candidate in candidates
        ]


def build_fixture_hybrid_retriever(config: RetrievalConfig | None = None, records: list[dict] | None = None) -> HybridRetriever:
    corpus = records or load_fixture_records()
    return HybridRetriever(
        qdrant_client=FixtureQdrantClient(corpus),
        embedder=FixtureEmbedder(),
        hydrator=FixtureHydrator(),
        sparse_retriever=FixtureSparseRetriever(corpus),
        sparse_scorer=FixtureSparseScorer(),
        reranker=FixtureReranker(),
        config=config or RetrievalConfig(),
    )

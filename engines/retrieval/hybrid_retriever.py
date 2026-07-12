from __future__ import annotations

from qdrant_client.http import models

from engines.retrieval.embedder import DeterministicEmbedder
from engines.retrieval.postgres_hydrator import PostgresHydrator
from engines.retrieval.qdrant_client import FinancialQdrantClient
from engines.retrieval.query_understanding import build_retrieval_plan
from engines.retrieval.reranker_client import RerankerClient


class HybridRetriever:
    def __init__(
        self,
        qdrant_client: FinancialQdrantClient | None = None,
        reranker: RerankerClient | None = None,
        embedder: DeterministicEmbedder | None = None,
        hydrator: PostgresHydrator | None = None,
    ) -> None:
        self.qdrant_client = qdrant_client or FinancialQdrantClient()
        self.reranker = reranker or RerankerClient()
        self.embedder = embedder or DeterministicEmbedder()
        self.hydrator = hydrator or PostgresHydrator()

    def retrieve(self, query: str, task_type: str | None = None, filters: dict | None = None, top_k: int = 5) -> dict:
        plan = build_retrieval_plan(query=query, task_type=task_type, filters=filters, top_k=top_k)
        query_vector = self.embedder.embed(plan["query"])
        query_filter = self._build_filter(plan["filters"])
        candidates: list[dict] = []
        for collection in plan["collections"]:
            hits = self.qdrant_client.search(collection=collection, vector=query_vector, limit=plan["top_n_retrieve"], query_filter=query_filter)
            for hit in hits:
                candidates.append(
                    {
                        "chunk_id": hit.payload.get("chunk_id", str(hit.id)),
                        "text": hit.payload.get("text", ""),
                        "payload": hit.payload,
                        "score": hit.score,
                    }
                )
        reranked = self.reranker.rerank(query=plan["query"], candidates=candidates, top_k=plan["top_k_rerank"])
        hydrated = self.hydrator.hydrate(reranked)
        hydrated.sort(
            key=lambda item: (
                float(item.get("rerank_score") or 0.0),
                int(item.get("source_timestamp") or 0),
            ),
            reverse=True,
        )
        return {"plan": plan, "contexts": self._resolve_viewpoint_conflicts(hydrated)}

    def _build_filter(self, filters: dict) -> models.Filter | None:
        if not filters:
            return None
        must = []
        for key, value in filters.items():
            if isinstance(value, list):
                must.append(models.FieldCondition(key=key, match=models.MatchAny(any=value)))
            else:
                must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        return models.Filter(must=must)

    @classmethod
    def _resolve_viewpoint_conflicts(cls, contexts: list[dict]) -> list[dict]:
        resolved: list[dict] = []
        latest_by_conflict_key: dict[str, str] = {}
        for item in contexts:
            source_type = item.get("source_type")
            if source_type not in {"bilibili_video_viewpoint", "bilibili_financial_event"}:
                resolved.append(item)
                continue
            if source_type == "bilibili_financial_event" and item.get("conflict_status") == "superseded":
                continue
            conflict_key = cls._build_conflict_key(item)
            polarity = cls._viewpoint_polarity(item.get("related_strategy"))
            if not conflict_key or polarity == "neutral":
                resolved.append(item)
                continue
            previous_polarity = latest_by_conflict_key.get(conflict_key)
            if previous_polarity is None:
                latest_by_conflict_key[conflict_key] = polarity
                resolved.append(item)
                continue
            if previous_polarity == polarity:
                resolved.append(item)
                continue
        return resolved

    @staticmethod
    def _build_conflict_key(item: dict) -> str | None:
        theme = str(item.get("related_theme") or "").strip()
        symbol = str(item.get("related_symbol") or "").strip()
        strategy = str(item.get("related_strategy") or "").strip()
        domain = "generic"
        if strategy in {"viewpoint_bull", "viewpoint_bear", "viewpoint_risk"}:
            domain = "stance"
        elif strategy == "viewpoint_actionable":
            domain = "actionable"
        elif strategy.startswith("event_"):
            domain = strategy
        if theme:
            return f"theme::{theme}::{domain}"
        if symbol:
            return f"symbol::{symbol}::{domain}"
        return None

    @staticmethod
    def _viewpoint_polarity(strategy: str | None) -> str:
        value = str(strategy or "").strip().lower()
        if value == "viewpoint_bull":
            return "positive"
        if value in {"viewpoint_bear", "viewpoint_risk"}:
            return "negative"
        if value.startswith("event_"):
            if any(token in value for token in ("risk", "bear")):
                return "negative"
            if any(token in value for token in ("catalyst", "trend", "price_level")):
                return "positive"
        return "neutral"

from __future__ import annotations

import math
from datetime import UTC, datetime

from qdrant_client.http import models

from engines.retrieval.embedder import LocalChineseNgramEmbedder, build_embedder
from engines.domain_result import DomainResultMeta
from engines.retrieval.config import RetrievalConfig
from engines.retrieval.filters import normalize_retrieval_filters
from engines.retrieval.postgres_hydrator import PostgresHydrator
from engines.retrieval.qdrant_client import FinancialQdrantClient
from engines.retrieval.query_understanding import build_retrieval_plan
from engines.retrieval.reranker_client import RerankerClient
from engines.retrieval.retrieval_policy import RetrievalPolicy, merge_policy_filters
from engines.retrieval.sparse_retriever import PostgresSparseRetriever, SparseBM25Scorer


class HybridRetriever:
    def __init__(
        self,
        qdrant_client: FinancialQdrantClient | None = None,
        reranker: RerankerClient | None = None,
        embedder: LocalChineseNgramEmbedder | None = None,
        hydrator: PostgresHydrator | None = None,
        sparse_scorer: SparseBM25Scorer | None = None,
        sparse_retriever: PostgresSparseRetriever | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.qdrant_client = qdrant_client or FinancialQdrantClient()
        self.reranker = reranker or RerankerClient()
        self.embedder = embedder or build_embedder()
        self.hydrator = hydrator or PostgresHydrator()
        self.sparse_scorer = sparse_scorer or SparseBM25Scorer()
        self.sparse_retriever = sparse_retriever or PostgresSparseRetriever()
        self.config = config or RetrievalConfig()

    def retrieve(self, query: str, task_type: str | None = None, filters: dict | None = None, top_k: int = 5) -> dict:
        plan = build_retrieval_plan(query=query, task_type=task_type, filters=normalize_retrieval_filters(filters), top_k=top_k)
        policy_filters = RetrievalPolicy.filters_for(plan["task_type"])
        plan["filters"] = merge_policy_filters(policy_filters, plan["filters"])
        query_vector = self.embedder.embed(plan["query"])
        query_filter = self._build_filter(plan["filters"])
        dense_candidates: list[dict] = []
        if self.config.dense_recall_enabled:
            for collection in plan["collections"]:
                hits = self.qdrant_client.search(collection=collection, vector=query_vector, limit=plan["top_n_retrieve"], query_filter=query_filter)
                for hit in hits:
                    dense_candidates.append({"chunk_id": hit.payload.get("chunk_id", str(hit.id)), "text": hit.payload.get("text", ""), "payload": hit.payload, "dense_score": hit.score, "score": hit.score, "recall_sources": ["dense"]})
        sparse_candidates = self.sparse_retriever.search(
            plan["query"],
            collections=plan["collections"],
            filters=plan["filters"],
            limit=plan["top_n_retrieve"],
        ) if self.config.sparse_recall_enabled else []
        candidates = self._merge_recall_candidates(dense_candidates, sparse_candidates)
        candidates = self._apply_quality_gate(candidates, plan["filters"])
        candidates = self.sparse_scorer.score_candidates(plan["query"], candidates) if self.config.bm25_score_enabled else [item | {"bm25_score": 0.0, "sparse_score_source": "disabled"} for item in candidates]
        reranked = self.reranker.rerank(query=plan["query"], candidates=candidates, top_k=plan["top_k_rerank"]) if self.config.reranker_enabled else candidates[: plan["top_k_rerank"]]
        reranked = self._merge_candidate_fields(candidates, reranked)
        reranked = self._apply_hybrid_score(reranked)
        hydrated = self.hydrator.hydrate(reranked)
        contexts = self._filter_expired_contexts(hydrated, plan)
        if self.config.conflict_resolution_enabled:
            contexts = self._resolve_viewpoint_conflicts(contexts)
            contexts = self._resolve_knowledge_conflicts(contexts)
        if self.config.source_priority_enabled:
            contexts = self._apply_source_priority(contexts, plan.get("preferred_source_types") or [])
        metadata = getattr(self.embedder, "metadata", None)
        if metadata is None:
            embedding = {"provider": "unknown", "model": type(self.embedder).__name__, "dimension": len(query_vector)}
        else:
            embedding = metadata.__dict__
        return {
            "plan": plan,
            "contexts": contexts,
            "embedding": embedding,
            "meta": DomainResultMeta(
                data_source="hybrid_retriever",
                calculation_version="retrieval_v1",
                warnings=["NO_CONTEXTS"] if not contexts else [],
            ).model_dump(mode="json"),
        }

    def _build_filter(self, filters: dict) -> models.Filter | None:
        if not filters:
            return None
        must = []
        must_not = []
        for key, value in filters.items():
            if key in {"valid_at", "valid_only"}:
                # valid_only is enforced by the python-side gate / sparse SQL layer;
                # dense payloads have no comparable field to filter on.
                continue
            if key == "denied_review_status":
                denied = [str(item) for item in (value if isinstance(value, list) else [value])]
                if denied:
                    must_not.append(models.FieldCondition(key="review_status", match=models.MatchAny(any=denied)))
                continue
            if key == "minimum_support_status":
                allowed = RetrievalPolicy.allowed_statuses(str(value))
                if allowed:
                    must.append(models.FieldCondition(key="support_status", match=models.MatchAny(any=allowed)))
                continue
            if key == "minimum_support_probability":
                must.append(models.FieldCondition(key="support_probability", range=models.Range(gte=float(value))))
                continue
            if isinstance(value, list):
                must.append(models.FieldCondition(key=key, match=models.MatchAny(any=value)))
            else:
                must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        if not must and not must_not:
            return None
        return models.Filter(must=must, must_not=must_not or None)

    @staticmethod
    def _apply_quality_gate(candidates: list[dict], filters: dict) -> list[dict]:
        minimum_probability = filters.get("minimum_support_probability")
        allowed = set(RetrievalPolicy.allowed_statuses(filters.get("minimum_support_status")))
        truth_status = filters.get("truth_status")
        allowed_truth = {str(item) for item in (truth_status if isinstance(truth_status, list) else [truth_status]) if item} if truth_status else set()
        denied_value = filters.get("denied_review_status")
        denied_review = {str(item).upper() for item in (denied_value if isinstance(denied_value, list) else [denied_value]) if item}
        denied_review.add("REJECTED")
        result = []
        for candidate in candidates:
            payload = candidate.get("payload") or {}
            if payload.get("postgres_table") != "knowledge_unit":
                result.append(candidate)
                continue
            if allowed and str(payload.get("support_status") or "").upper() not in allowed:
                continue
            try:
                if minimum_probability is not None and float(payload.get("support_probability")) < float(minimum_probability):
                    continue
            except (TypeError, ValueError):
                continue
            if allowed_truth and payload.get("truth_status") not in allowed_truth:
                continue
            review_status = str(payload.get("review_status") or "").upper()
            if review_status and review_status in denied_review:
                continue
            result.append(candidate)
        return result

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

    @classmethod
    def _apply_source_priority(cls, contexts: list[dict], preferred_source_types: list[str]) -> list[dict]:
        priority_map = {source_type: len(preferred_source_types) - index for index, source_type in enumerate(preferred_source_types)}
        contexts.sort(
            key=lambda item: (
                float(item.get("final_score") if item.get("final_score") is not None else item.get("rerank_score") or 0.0)
                + cls._source_priority_bonus(item.get("source_type"), priority_map),
                int(item.get("source_timestamp") or 0),
            ),
            reverse=True,
        )
        return contexts

    def _apply_hybrid_score(self, items: list[dict]) -> list[dict]:
        for item in items:
            payload = item.get("payload") or {}
            dense = float(item.get("dense_score") or item.get("score") or 0.0)
            bm25 = float(item.get("bm25_score") or 0.0)
            rerank = float(item.get("rerank_score") or 0.0)
            source_quality = float(payload.get("source_quality") or 0.5)
            freshness = float(payload.get("freshness_score") or self._freshness_score(payload)) if self.config.freshness_score_enabled else 0.0
            status = self._status_score(payload.get("status"))
            support = float(payload.get("support_probability") or 0.0) if payload.get("postgres_table") == "knowledge_unit" else 0.5
            if self._is_expired(payload):
                status = 0.0
                freshness = 0.0
            item["final_score"] = round(
                0.35 * dense
                + 0.20 * bm25
                + 0.30 * rerank
                + 0.05 * source_quality
                + (0.05 * freshness if self.config.freshness_score_enabled else 0.0)
                + 0.05 * status
                + (0.05 * support if payload.get("postgres_table") == "knowledge_unit" else 0.0),
                6,
            )
        items.sort(key=lambda entry: entry.get("final_score", 0.0), reverse=True)
        return items

    @staticmethod
    def _merge_candidate_fields(candidates: list[dict], reranked: list[dict]) -> list[dict]:
        by_chunk = {str(item.get("chunk_id")): item for item in candidates}
        merged = []
        for item in reranked:
            base = by_chunk.get(str(item.get("chunk_id")), {})
            merged.append({**base, **item})
        return merged

    @staticmethod
    def _merge_recall_candidates(dense: list[dict], sparse: list[dict]) -> list[dict]:
        merged: dict[str, dict] = {}
        for rank, item in enumerate(dense, start=1):
            chunk_id = str(item.get("chunk_id"))
            merged[chunk_id] = item | {"dense_rank": rank}
        for rank, item in enumerate(sparse, start=1):
            chunk_id = str(item.get("chunk_id"))
            existing = merged.get(chunk_id)
            if existing is None:
                merged[chunk_id] = item | {"sparse_rank": rank}
                continue
            sources = sorted(set(existing.get("recall_sources") or []) | set(item.get("recall_sources") or ["sparse"]))
            existing.update(
                {
                    "text": existing.get("text") or item.get("text"),
                    "payload": {**(item.get("payload") or {}), **(existing.get("payload") or {})},
                    "sparse_recall_score": item.get("sparse_recall_score", 0.0),
                    "sparse_rank": rank,
                    "recall_sources": sources,
                }
            )
        return list(merged.values())

    @staticmethod
    def _status_score(status: str | None) -> float:
        value = str(status or "").lower()
        if value in {"approved", "validated", "active", "current"}:
            return 1.0
        if value in {"superseded", "retired"}:
            return 0.1
        return 0.5

    @staticmethod
    def _freshness_score(payload: dict) -> float:
        if payload.get("source_type") == "video_knowledge_unit":
            as_of = HybridRetriever._parse_datetime(payload.get("source_date") or payload.get("as_of_time"))
            half_life = float(payload.get("decay_half_life_days") or 0.0)
            if as_of and half_life > 0:
                age_days = max((datetime.now(UTC) - as_of).total_seconds() / 86400, 0.0)
                return max(min(math.pow(0.5, age_days / half_life), 1.0), 0.0)
            return 0.8 if not payload.get("valid_to") else 0.6
        if payload.get("valid_to"):
            return 0.2
        return 0.7

    @classmethod
    def _filter_expired_contexts(cls, contexts: list[dict], plan: dict) -> list[dict]:
        task_type = plan.get("task_type")
        if task_type not in {"current_state", "trading_decision", "market_opportunity_scan", "strategy_question"}:
            return contexts
        return [item for item in contexts if not cls._is_expired(item.get("record") or item)]

    @classmethod
    def _resolve_knowledge_conflicts(cls, contexts: list[dict]) -> list[dict]:
        best_by_group: dict[str, dict] = {}
        passthrough: list[dict] = []
        for item in contexts:
            record = item.get("record") or {}
            group_id = record.get("conflict_group_id") or (item.get("payload") or {}).get("conflict_group_id")
            if item.get("source_type") != "video_knowledge_unit" or not group_id:
                passthrough.append(item)
                continue
            current = best_by_group.get(str(group_id))
            if current is None or cls._knowledge_conflict_rank(item) > cls._knowledge_conflict_rank(current):
                best_by_group[str(group_id)] = item
        return passthrough + list(best_by_group.values())

    @staticmethod
    def _knowledge_conflict_rank(item: dict) -> tuple[float, int]:
        record = item.get("record") or {}
        status = HybridRetriever._status_score(record.get("status"))
        final_score = float(item.get("final_score") or 0.0)
        source_ts = int(item.get("source_timestamp") or 0)
        return (status + final_score, source_ts)

    @classmethod
    def _is_expired(cls, payload: dict) -> bool:
        valid_to = cls._parse_datetime(payload.get("valid_to"))
        return valid_to is not None and valid_to < datetime.now(UTC)

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _source_priority_bonus(source_type: str | None, priority_map: dict[str, int]) -> float:
        if not source_type:
            return 0.0
        weight = priority_map.get(str(source_type), 0)
        return weight * 0.02

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

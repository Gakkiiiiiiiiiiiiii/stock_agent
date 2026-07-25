from __future__ import annotations

import math
import logging
from collections import Counter
from typing import Any

from sqlalchemy import text

from storage.db import session_scope

logger = logging.getLogger(__name__)


class SparseBM25Scorer:
    def score_candidates(self, query: str, candidates: list[dict]) -> list[dict]:
        docs = [_tokenize(item.get("text") or (item.get("payload") or {}).get("text") or "") for item in candidates]
        query_terms = _tokenize(query)
        if not candidates or not query_terms:
            return [item | {"bm25_score": 0.0, "sparse_score_source": "bm25_empty"} for item in candidates]
        avgdl = sum(len(doc) for doc in docs) / max(len(docs), 1)
        df = Counter(term for doc in docs for term in set(doc))
        scored = []
        for item, doc in zip(candidates, docs, strict=False):
            tf = Counter(doc)
            score = 0.0
            for term in query_terms:
                if tf[term] <= 0:
                    continue
                idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
                denom = tf[term] + 1.2 * (1 - 0.75 + 0.75 * len(doc) / max(avgdl, 1e-9))
                score += idf * (tf[term] * 2.2) / denom
            scored.append(item | {"bm25_score": round(score, 6), "sparse_score_source": "bm25_candidate_text"})
        return scored


class PostgresSparseRetriever:
    """Independent sparse recall over persisted memory records.

    PostgreSQL uses built-in full text search. SQLite test/local fallback uses
    LIKE over the same persisted records so sparse recall still has an
    independent candidate path when Postgres is not available.
    """

    def search(
        self,
        query: str,
        collections: list[str],
        filters: dict | None = None,
        limit: int = 20,
    ) -> list[dict]:
        query = str(query or "").strip()
        if not query or not collections or limit <= 0:
            return []
        try:
            with session_scope() as session:
                dialect = session.bind.dialect.name if session.bind is not None else ""
                if dialect == "postgresql":
                    rows = session.execute(
                        text(_postgres_sparse_sql(collections, filters)),
                        _sparse_params(query, collections, filters, limit),
                    ).mappings().all()
                else:
                    rows = session.execute(
                        text(_sqlite_sparse_sql(collections, filters)),
                        _sparse_params(query, collections, filters, limit),
                    ).mappings().all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sparse recall failed: %s", exc)
            return []
        return [_candidate_from_row(row) for row in rows]


def _postgres_sparse_sql(collections: list[str], filters: dict | None) -> str:
    clauses = [
        "mr.is_deleted = false",
        "vim.index_status = 'indexed'",
        f"vim.qdrant_collection IN ({_placeholders('collection', collections)})",
        """
        to_tsvector('simple', coalesce(mr.title, '') || ' ' || coalesce(mr.content, ''))
        @@ plainto_tsquery('simple', :query)
        """,
    ]
    clauses.extend(_filter_clauses(filters))
    where_sql = " AND ".join(f"({clause})" for clause in clauses)
    return f"""
        SELECT
            mr.id AS postgres_id,
            mr.memory_type,
            mr.title,
            mr.content,
            mr.source_type,
            mr.status,
            mr.related_regime,
            mr.related_strategy,
            mr.related_theme,
            mr.related_symbol,
            mr.source_date,
            mr.valid_from,
            mr.valid_to,
            vim.chunk_id,
            vim.qdrant_collection,
            ts_rank_cd(
                to_tsvector('simple', coalesce(mr.title, '') || ' ' || coalesce(mr.content, '')),
                plainto_tsquery('simple', :query)
            ) AS sparse_recall_score
        FROM memory_record mr
        JOIN vector_index_mapping vim
          ON vim.postgres_table = 'memory_record' AND vim.postgres_id = mr.id
        WHERE {where_sql}
        ORDER BY sparse_recall_score DESC, mr.created_at DESC
        LIMIT :limit
    """


def _sqlite_sparse_sql(collections: list[str], filters: dict | None) -> str:
    clauses = [
        "coalesce(mr.is_deleted, 0) = 0",
        "vim.index_status = 'indexed'",
        f"vim.qdrant_collection IN ({_placeholders('collection', collections)})",
        "lower(coalesce(mr.title, '') || ' ' || coalesce(mr.content, '')) LIKE :like_query",
    ]
    clauses.extend(_filter_clauses(filters))
    where_sql = " AND ".join(f"({clause})" for clause in clauses)
    return f"""
        SELECT
            mr.id AS postgres_id,
            mr.memory_type,
            mr.title,
            mr.content,
            mr.source_type,
            mr.status,
            mr.related_regime,
            mr.related_strategy,
            mr.related_theme,
            mr.related_symbol,
            mr.source_date,
            mr.valid_from,
            mr.valid_to,
            vim.chunk_id,
            vim.qdrant_collection,
            1.0 AS sparse_recall_score
        FROM memory_record mr
        JOIN vector_index_mapping vim
          ON vim.postgres_table = 'memory_record' AND vim.postgres_id = mr.id
        WHERE {where_sql}
        ORDER BY mr.created_at DESC
        LIMIT :limit
    """


def _filter_clauses(filters: dict | None) -> list[str]:
    allowed = {"memory_type", "source_type", "status", "related_regime", "related_strategy", "related_theme", "related_symbol"}
    clauses = []
    for key, value in (filters or {}).items():
        if key not in allowed:
            continue
        if isinstance(value, list):
            clauses.append("1 = 0" if not value else f"mr.{key} IN ({_placeholders(f'filter_{key}', value)})")
        else:
            clauses.append(f"mr.{key} = :filter_{key}")
    return clauses


def _sparse_params(query: str, collections: list[str], filters: dict | None, limit: int) -> dict[str, Any]:
    params: dict[str, Any] = {
        "query": query,
        "like_query": f"%{query.lower()}%",
        "limit": int(limit),
    }
    for index, collection in enumerate(collections):
        params[f"collection_{index}"] = collection
    for key, value in (filters or {}).items():
        if key in {"memory_type", "source_type", "status", "related_regime", "related_strategy", "related_theme", "related_symbol"}:
            if isinstance(value, list):
                for index, item in enumerate(value):
                    params[f"filter_{key}_{index}"] = item
            else:
                params[f"filter_{key}"] = value
    return params


def _placeholders(prefix: str, values: list) -> str:
    return ", ".join(f":{prefix}_{index}" for index, _ in enumerate(values))


def _candidate_from_row(row) -> dict:
    payload = {
        "postgres_table": "memory_record",
        "postgres_id": row["postgres_id"],
        "chunk_id": row["chunk_id"],
        "text": row["content"],
        "title": row["title"],
        "memory_type": row["memory_type"],
        "source_type": row["source_type"],
        "status": row["status"],
        "related_regime": row["related_regime"],
        "related_strategy": row["related_strategy"],
        "related_theme": row["related_theme"],
        "related_symbol": row["related_symbol"],
        "source_date": _iso(row["source_date"]),
        "source_timestamp": int(row["source_date"].timestamp()) if row["source_date"] else None,
        "valid_from": _iso(row["valid_from"]),
        "valid_to": _iso(row["valid_to"]),
        "qdrant_collection": row["qdrant_collection"],
    }
    return {
        "chunk_id": row["chunk_id"],
        "text": row["content"],
        "payload": payload,
        "dense_score": 0.0,
        "score": 0.0,
        "sparse_recall_score": float(row["sparse_recall_score"] or 0.0),
        "recall_sources": ["sparse"],
    }


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _tokenize(text: str) -> list[str]:
    compact = "".join(str(text or "").lower().split())
    words = str(text or "").lower().split()
    chars = list(compact)
    bigrams = [compact[i : i + 2] for i in range(max(len(compact) - 1, 0))]
    return words + chars + bigrams

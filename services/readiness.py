"""Small dependency checks shared by independently deployed services."""
from __future__ import annotations

import os

import httpx
from sqlalchemy import text

from storage.db import session_scope


def postgres_check() -> str:
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "failed"


def http_check(url: str) -> str:
    try:
        response = httpx.get(url, timeout=3)
        response.raise_for_status()
        return "ok"
    except Exception:
        return "failed"


def retrieval_checks() -> dict[str, str]:
    embedding = os.getenv("EMBEDDING_BASE_URL", "http://localhost:8001/v1").rstrip("/")
    embedding_health = embedding[:-3] if embedding.endswith("/v1") else embedding
    return {
        "postgres": postgres_check(),
        "qdrant": http_check(f"{os.getenv('QDRANT_URL', 'http://localhost:6333').rstrip('/')}/collections"),
        "embedding": http_check(f"{embedding_health}/health"),
        "reranker": http_check(f"{os.getenv('RERANKER_URL', 'http://localhost:8010').rstrip('/')}/health"),
    }

from __future__ import annotations

from collections import Counter
import os
from functools import lru_cache
import math

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Local Reranker")


class RerankRequest(BaseModel):
    query: str
    candidates: list[dict]
    top_k: int = 5


def _tokenize(text: str) -> list[str]:
    compact = "".join(str(text or "").lower().split())
    words = str(text or "").lower().split()
    chars = list(compact)
    bigrams = [compact[i : i + 2] for i in range(max(len(compact) - 1, 0))]
    trigrams = [compact[i : i + 3] for i in range(max(len(compact) - 2, 0))]
    return words + chars + bigrams + trigrams


def _token_score(query: str, text: str) -> float:
    q = Counter(_tokenize(query))
    t = Counter(_tokenize(text))
    shared = sum(min(q[token], t[token]) for token in q)
    return shared / max(sum(q.values()), 1)


@lru_cache(maxsize=1)
def _cross_encoder():
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError("sentence-transformers is required for semantic reranker") from exc
    return CrossEncoder(os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"))


@app.get("/health")
def health() -> dict:
    provider = os.getenv("RERANKER_PROVIDER", "local_ngram")
    return {
        "status": "ok",
        "provider": provider,
        "model": os.getenv("RERANKER_MODEL", "local-chinese-ngram-reranker"),
        "mode": "semantic" if provider in {"sentence_transformers", "cross_encoder"} else "fallback",
        "semantic": provider in {"sentence_transformers", "cross_encoder"},
    }


@app.post("/rerank")
def rerank(request: RerankRequest) -> dict:
    ranked = []
    provider = os.getenv("RERANKER_PROVIDER", "local_ngram")
    semantic_scores = None
    if provider in {"sentence_transformers", "cross_encoder"}:
        pairs = [(request.query, candidate.get("text", "")) for candidate in request.candidates]
        raw_scores = [float(value) for value in _cross_encoder().predict(pairs)]
        semantic_scores = _normalize_scores(raw_scores)
    for index, candidate in enumerate(request.candidates):
        payload = candidate.get("payload", {})
        status_bonus = 0.2 if payload.get("status") in {"approved", "validated"} else 0.0
        semantic_score = semantic_scores[index] if semantic_scores is not None else _token_score(request.query, candidate.get("text", ""))
        score = semantic_score + status_bonus
        ranked.append(
            {
                **candidate,
                "chunk_id": candidate["chunk_id"],
                "rerank_score": round(score, 4),
                "semantic_score": round(semantic_score, 4),
                "payload": payload,
                "text": candidate.get("text", ""),
                "mode": "semantic" if semantic_scores is not None else "fallback",
                "semantic": semantic_scores is not None,
            }
        )
    ranked.sort(key=lambda item: item["rerank_score"], reverse=True)
    return {"reranked": ranked[: request.top_k]}


def _normalize_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    sigmoid = [1.0 / (1.0 + math.exp(-max(min(value, 60.0), -60.0))) for value in values]
    low, high = min(sigmoid), max(sigmoid)
    if abs(high - low) < 1e-12:
        return [round(value, 6) for value in sigmoid]
    return [round((value - low) / (high - low), 6) for value in sigmoid]

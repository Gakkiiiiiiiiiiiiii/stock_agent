from __future__ import annotations

from collections import Counter

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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/rerank")
def rerank(request: RerankRequest) -> dict:
    ranked = []
    for candidate in request.candidates:
        payload = candidate.get("payload", {})
        status_bonus = 0.2 if payload.get("status") in {"approved", "validated"} else 0.0
        semantic_score = _token_score(request.query, candidate.get("text", ""))
        score = semantic_score + status_bonus
        ranked.append(
            {
                "chunk_id": candidate["chunk_id"],
                "rerank_score": round(score, 4),
                "semantic_score": round(semantic_score, 4),
                "payload": payload,
                "text": candidate.get("text", ""),
            }
        )
    ranked.sort(key=lambda item: item["rerank_score"], reverse=True)
    return {"reranked": ranked[: request.top_k]}

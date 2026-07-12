from __future__ import annotations

from collections import Counter

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Local Reranker")


class RerankRequest(BaseModel):
    query: str
    candidates: list[dict]
    top_k: int = 5


def _token_score(query: str, text: str) -> float:
    q = Counter(query.lower().split())
    t = Counter(text.lower().split())
    shared = sum(min(q[token], t[token]) for token in q)
    return shared / max(len(q), 1)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/rerank")
def rerank(request: RerankRequest) -> dict:
    ranked = []
    for candidate in request.candidates:
        payload = candidate.get("payload", {})
        status_bonus = 0.2 if payload.get("status") in {"approved", "validated"} else 0.0
        score = _token_score(request.query, candidate.get("text", "")) + status_bonus
        ranked.append(
            {
                "chunk_id": candidate["chunk_id"],
                "rerank_score": round(score, 4),
                "payload": payload,
                "text": candidate.get("text", ""),
            }
        )
    ranked.sort(key=lambda item: item["rerank_score"], reverse=True)
    return {"reranked": ranked[: request.top_k]}


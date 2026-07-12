from __future__ import annotations

import os
from typing import Any

import httpx


class RerankerClient:
    def __init__(self, base_url: str | None = None, client: httpx.Client | None = None) -> None:
        self.base_url = (base_url or os.getenv("RERANKER_URL", "http://localhost:8010")).rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def rerank(self, query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
        response = self.client.post(
            f"{self.base_url}/rerank",
            json={"query": query, "candidates": candidates, "top_k": top_k},
        )
        response.raise_for_status()
        return response.json()["reranked"]


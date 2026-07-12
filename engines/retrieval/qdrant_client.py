from __future__ import annotations

import os
from uuid import NAMESPACE_URL, uuid4, uuid5

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models

from financial_agent.config import load_yaml_config


class FinancialQdrantClient:
    def __init__(self, url: str | None = None, api_key: str | None = None) -> None:
        self.url = url or os.getenv("QDRANT_URL", "http://localhost:6333")
        self.api_key = api_key or os.getenv("QDRANT_API_KEY")
        self.client = QdrantClient(url=self.url, api_key=self.api_key)
        self.config = load_yaml_config("qdrant.yaml")["qdrant"]

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["api-key"] = self.api_key
        return headers

    def ensure_collections(self) -> None:
        response = requests.get(f"{self.url}/collections", headers=self._headers(), timeout=30)
        response.raise_for_status()
        collections = {
            item["name"]
            for item in (response.json().get("result") or {}).get("collections", [])
        }
        for collection_name, config in self.config["collections"].items():
            if collection_name not in collections:
                create_response = requests.put(
                    f"{self.url}/collections/{collection_name}",
                    headers=self._headers(),
                    json={
                        "vectors": {
                            "size": config["vector_size"],
                            "distance": config["distance"].upper(),
                        }
                    },
                    timeout=30,
                )
                create_response.raise_for_status()
            for field in self.config.get("payload_indexes", []):
                try:
                    index_response = requests.put(
                        f"{self.url}/collections/{collection_name}/index",
                        headers=self._headers(),
                        params={"wait": "true"},
                        json={"field_name": field, "field_schema": "keyword"},
                        timeout=30,
                    )
                    index_response.raise_for_status()
                except Exception:
                    pass

    def upsert_chunk(self, collection: str, vector: list[float], payload: dict) -> str:
        chunk_id = payload.get("chunk_id")
        point_id = str(uuid5(NAMESPACE_URL, str(chunk_id))) if chunk_id else str(uuid4())
        response = requests.put(
            f"{self.url}/collections/{collection}/points",
            params={"wait": "true"},
            headers=self._headers(),
            json={
                "points": [
                    {
                        "id": point_id,
                        "vector": vector,
                        "payload": payload,
                    }
                ]
            },
            timeout=30,
        )
        response.raise_for_status()
        return point_id

    def delete_by_payload(self, collection: str, filters: dict) -> None:
        must = [{"key": key, "match": {"value": value}} for key, value in filters.items()]
        response = requests.post(
            f"{self.url}/collections/{collection}/points/delete",
            params={"wait": "true"},
            headers=self._headers(),
            json={"filter": {"must": must}},
            timeout=30,
        )
        response.raise_for_status()

    def search(self, collection: str, vector: list[float], limit: int, query_filter: models.Filter | None = None):
        response = self.client.query_points(
            collection_name=collection,
            query=vector,
            limit=limit,
            query_filter=query_filter,
        )
        return getattr(response, "points", response)

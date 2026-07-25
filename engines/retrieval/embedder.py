from __future__ import annotations

import math
import os
from collections import Counter
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class EmbeddingMetadata:
    provider: str
    model: str
    dimension: int
    semantic: bool


class EmbeddingError(RuntimeError):
    pass


class OpenAICompatibleEmbedder:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("EMBEDDING_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("EMBEDDING_API_KEY", "")
        self.model = model or os.getenv("EMBEDDING_MODEL", "Qwen3-Embedding")
        self.dimension = int(dimension or os.getenv("EMBEDDING_DIMENSION", "1024"))
        self.client = client or httpx.Client(timeout=60)
        if not self.base_url:
            raise EmbeddingError("EMBEDDING_BASE_URL is required for openai_compatible embedding provider")
        self._metadata = self._load_service_metadata()

    @property
    def metadata(self) -> EmbeddingMetadata:
        return self._metadata

    def embed(self, text: str) -> list[float]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = self.client.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json={"model": self.model, "input": text},
        )
        response.raise_for_status()
        data = response.json()
        vector = data["data"][0]["embedding"]
        if len(vector) != self.dimension:
            raise EmbeddingError(f"embedding dimension mismatch: got {len(vector)}, expected {self.dimension}")
        return [float(v) for v in vector]

    def _load_service_metadata(self) -> EmbeddingMetadata:
        root_url = self.base_url.removesuffix("/v1")
        try:
            response = self.client.get(f"{root_url}/health")
            response.raise_for_status()
            payload = response.json()
            return EmbeddingMetadata(
                str(payload.get("provider") or "openai_compatible"),
                str(payload.get("model") or self.model),
                int(payload.get("dimension") or self.dimension),
                bool(payload.get("semantic", True)),
            )
        except Exception:
            return EmbeddingMetadata("openai_compatible", self.model, self.dimension, True)


class SentenceTransformersEmbedder:
    def __init__(self, model: str | None = None, dimension: int | None = None) -> None:
        self.model = model or os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
        self.dimension = int(dimension or os.getenv("EMBEDDING_DIMENSION", "1024"))
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError("sentence-transformers is required for sentence_transformers embedding provider") from exc
        self._model = SentenceTransformer(self.model)

    @property
    def metadata(self) -> EmbeddingMetadata:
        return EmbeddingMetadata("sentence_transformers", self.model, self.dimension, True)

    def embed(self, text: str) -> list[float]:
        vector = self._model.encode([text], normalize_embeddings=True)[0].tolist()
        if len(vector) != self.dimension:
            raise EmbeddingError(f"embedding dimension mismatch: got {len(vector)}, expected {self.dimension}")
        return [float(v) for v in vector]


class LocalChineseNgramEmbedder:
    """Deterministic lexical fallback for local tests; not a semantic model."""

    def __init__(self, vector_size: int = 384, model: str = "local-chinese-ngram-v1") -> None:
        self.vector_size = vector_size
        self.model = model

    @property
    def metadata(self) -> EmbeddingMetadata:
        return EmbeddingMetadata("local_ngram", self.model, self.vector_size, False)

    def embed(self, text: str) -> list[float]:
        tokens = _char_ngrams(text)
        counts = Counter(tokens)
        values = [0.0] * self.vector_size
        for token, count in counts.items():
            idx = _stable_index(token, self.vector_size)
            values[idx] += float(count)
        norm = math.sqrt(sum(v * v for v in values))
        if norm <= 0:
            return values
        return [round(v / norm, 8) for v in values]


def build_embedder(provider: str | None = None) -> OpenAICompatibleEmbedder | LocalChineseNgramEmbedder:
    resolved = (provider or os.getenv("EMBEDDING_PROVIDER", "local_ngram")).strip().lower()
    if resolved in {"openai", "openai_compatible", "compatible"}:
        return OpenAICompatibleEmbedder()
    if resolved in {"sentence_transformers", "sentence-transformers", "bge_m3"}:
        return SentenceTransformersEmbedder()
    if resolved in {"local", "local_ngram", "dev"}:
        return LocalChineseNgramEmbedder(vector_size=int(os.getenv("EMBEDDING_DIMENSION", "384")))
    raise EmbeddingError(f"unsupported embedding provider: {resolved}")


def _char_ngrams(text: str) -> list[str]:
    compact = "".join(str(text or "").lower().split())
    if not compact:
        return []
    tokens = list(compact)
    tokens.extend(compact[i : i + 2] for i in range(max(len(compact) - 1, 0)))
    tokens.extend(compact[i : i + 3] for i in range(max(len(compact) - 2, 0)))
    return tokens


def _stable_index(token: str, size: int) -> int:
    value = 2166136261
    for char in token:
        value ^= ord(char)
        value = (value * 16777619) & 0xFFFFFFFF
    return value % size


# Backward-compatible alias for existing tests/imports.
DeterministicEmbedder = LocalChineseNgramEmbedder


__all__ = [
    "EmbeddingMetadata",
    "EmbeddingError",
    "OpenAICompatibleEmbedder",
    "SentenceTransformersEmbedder",
    "LocalChineseNgramEmbedder",
    "DeterministicEmbedder",
    "build_embedder",
]

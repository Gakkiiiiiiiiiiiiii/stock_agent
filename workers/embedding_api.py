from __future__ import annotations

import os
import time
from functools import lru_cache

from fastapi import FastAPI
from pydantic import BaseModel

from engines.retrieval.embedder import EmbeddingMetadata, LocalChineseNgramEmbedder, build_embedder

app = FastAPI(title="Local OpenAI-Compatible Embedding Service")


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None


@app.get("/health")
def health() -> dict:
    embedder = get_embedder()
    metadata = _metadata(embedder)
    return {"status": "ok", **metadata.__dict__}


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest) -> dict:
    embedder = get_embedder()
    metadata = _metadata(embedder)
    provider = metadata.provider
    configured_model = metadata.model
    if request.model and request.model != configured_model:
        return {"error": {"code": "EMBEDDING_MODEL_MISMATCH", "message": "request model must match service model", "expected": configured_model, "actual": request.model}}
    items = request.input if isinstance(request.input, list) else [request.input]
    return {
        "object": "list",
        "model": configured_model,
        "provider": provider,
        "semantic": metadata.semantic,
        "data": [
            {
                "object": "embedding",
                "index": index,
                "embedding": embedder.embed(text),
            }
            for index, text in enumerate(items)
        ],
        "usage": {
            "prompt_tokens": sum(len(str(item)) for item in items),
            "total_tokens": sum(len(str(item)) for item in items),
        },
        "created": int(time.time()),
    }


@lru_cache(maxsize=1)
def get_embedder():
    provider = os.getenv("EMBEDDING_PROVIDER", "local_ngram")
    model = os.getenv("EMBEDDING_MODEL", "local-chinese-ngram-v1")
    dimension = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
    if provider.lower() in {"local", "local_ngram", "dev"}:
        if model == "BAAI/bge-m3":
            model = "local-chinese-ngram-v1"
        return LocalChineseNgramEmbedder(vector_size=dimension, model=model)
    return build_embedder(provider)


def _metadata(embedder) -> EmbeddingMetadata:
    metadata = getattr(embedder, "metadata", None)
    if metadata is not None:
        return metadata
    return EmbeddingMetadata(
        provider=os.getenv("EMBEDDING_PROVIDER", "local_ngram"),
        model=os.getenv("EMBEDDING_MODEL", type(embedder).__name__),
        dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
        semantic=os.getenv("EMBEDDING_PROVIDER", "local_ngram").lower() not in {"local", "local_ngram", "dev"},
    )

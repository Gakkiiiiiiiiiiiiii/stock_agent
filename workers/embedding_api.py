from __future__ import annotations

import os
import time

from fastapi import FastAPI
from pydantic import BaseModel

from engines.retrieval.embedder import LocalChineseNgramEmbedder, build_embedder

app = FastAPI(title="Local OpenAI-Compatible Embedding Service")


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None


@app.get("/health")
def health() -> dict:
    provider = os.getenv("EMBEDDING_PROVIDER", "local_ngram")
    model = os.getenv("EMBEDDING_MODEL", "local-chinese-ngram-v1")
    dimension = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
    semantic = provider.lower() in {"sentence_transformers", "sentence-transformers", "bge_m3", "openai", "openai_compatible", "compatible"}
    return {"status": "ok", "provider": provider, "model": model, "dimension": dimension, "semantic": semantic}


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest) -> dict:
    dimension = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
    provider = os.getenv("EMBEDDING_PROVIDER", "local_ngram")
    configured_model = os.getenv("EMBEDDING_MODEL", "local-chinese-ngram-v1")
    if request.model and request.model != configured_model:
        return {"error": {"code": "EMBEDDING_MODEL_MISMATCH", "message": "request model must match service model", "expected": configured_model, "actual": request.model}}
    model = configured_model
    if provider.lower() in {"local", "local_ngram", "dev"} and model == "BAAI/bge-m3":
        model = "local-chinese-ngram-v1"
    embedder = LocalChineseNgramEmbedder(vector_size=dimension, model=model) if provider.lower() in {"local", "local_ngram", "dev"} else build_embedder(provider)
    items = request.input if isinstance(request.input, list) else [request.input]
    return {
        "object": "list",
        "model": model,
        "provider": provider,
        "semantic": provider.lower() not in {"local", "local_ngram", "dev"},
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

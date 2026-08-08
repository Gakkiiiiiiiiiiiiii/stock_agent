from __future__ import annotations

from pydantic import BaseModel


class RetrievalConfig(BaseModel):
    dense_enabled: bool = True
    sparse_enabled: bool = True
    reranker_enabled: bool = True
    freshness_enabled: bool = True
    source_priority_enabled: bool = True
    conflict_resolution_enabled: bool = True

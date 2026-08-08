from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RetrievalFilter(BaseModel):
    source_types: list[str] | None = None
    memory_types: list[str] | None = None
    symbols: list[str] | None = None
    themes: list[str] | None = None
    valid_at: datetime | None = None

    def backend_filters(self) -> dict:
        values: dict = {}
        if self.source_types:
            values["source_type"] = self.source_types
        if self.memory_types:
            values["memory_type"] = self.memory_types
        if self.symbols:
            values["related_symbol"] = self.symbols
        if self.themes:
            values["related_theme"] = self.themes
        # `valid_at` is retained for consumers that can apply temporal ranges;
        # existing dense/sparse stores keep their established expiry policies.
        if self.valid_at:
            values["valid_at"] = self.valid_at.isoformat()
        return values


def normalize_retrieval_filters(filters: dict | RetrievalFilter | None) -> dict:
    if filters is None:
        return {}
    if isinstance(filters, RetrievalFilter):
        return filters.backend_filters()
    aliases = {
        "source_types": "source_type",
        "memory_types": "memory_type",
        "symbols": "related_symbol",
        "themes": "related_theme",
    }
    result = {}
    for key, value in filters.items():
        result[aliases.get(key, key)] = value
    return result

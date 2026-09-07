"""Explicit safe model adapter for deployments without a provider binding."""
from __future__ import annotations

from app.ports.knowledge_conclusion_model import (
    StructuredModelRequest,
    StructuredModelResponse,
    StructuredModelUnavailable,
)


class UnavailableStructuredModel:
    """A composition-visible absence, never an environment-derived provider."""

    is_available = False

    def complete(self, _request: StructuredModelRequest) -> StructuredModelResponse:
        raise StructuredModelUnavailable("no injected model provider")

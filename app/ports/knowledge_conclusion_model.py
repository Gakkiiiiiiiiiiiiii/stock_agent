"""Injected structured-model boundary for content-only conclusions.

The provider idempotency key deliberately belongs to the request contract: an
ambiguous provider outcome may be retried, but is still one business attempt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class StructuredModelUnavailable(RuntimeError):
    """The structured-model boundary could not return a usable response."""


@dataclass(frozen=True)
class StructuredModelRequest:
    model_request_id: str
    provider_idempotency_key: str
    system_prompt: str
    user_json: str
    repair: bool = False


@dataclass(frozen=True)
class StructuredModelResponse:
    """Unvalidated provider JSON and its observed provider identity."""

    structured_json: dict[str, Any]
    provider_response_id: str
    provider: str
    model: str


class KnowledgeConclusionStructuredModel(Protocol):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResponse: ...

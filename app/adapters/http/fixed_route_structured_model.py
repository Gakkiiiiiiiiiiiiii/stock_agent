"""Bounded local fixture adapter for content-only structured synthesis."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from app.ports.knowledge_conclusion_model import (
    StructuredModelRequest,
    StructuredModelResponse,
    StructuredModelUnavailable,
)

_FIXTURE_ENDPOINT = "http://fixture-model:9000/v1/chat/completions"
_FIXTURE_HEALTH_ENDPOINT = "http://fixture-model:9000/health"
_MAX_RESPONSE_BYTES = 64 * 1024


class FixtureStructuredModel:
    """A fixed-route, no-tools adapter used only by the isolated fixture stack.

    Production provider configuration is intentionally not inferred here.  A
    deployment either installs an explicitly reviewed provider adapter or
    remains unavailable and uses the deterministic research fallback.
    """

    def __init__(self, *, credential_file: Path, endpoint: str = _FIXTURE_ENDPOINT, timeout_seconds: float = 3.0) -> None:
        if endpoint != _FIXTURE_ENDPOINT or not 0 < timeout_seconds <= 10:
            raise ValueError("FIXTURE_MODEL_ROUTE_INVALID")
        self._endpoint = endpoint
        self._timeout = timeout_seconds
        self._credential_file = credential_file

    def complete(self, request: StructuredModelRequest) -> StructuredModelResponse:
        try:
            credential = self._credential_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise StructuredModelUnavailable("fixture credential unavailable") from exc
        if not credential:
            raise StructuredModelUnavailable("fixture credential unavailable")
        payload = {
            "model": "fixture-structured-model",
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_json},
            ],
            "response_format": {"type": "json_object"},
            "tools": [],
        }
        try:
            response = httpx.post(
                self._endpoint,
                headers={"Authorization": "Bearer " + credential, "Idempotency-Key": request.provider_idempotency_key},
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            if len(response.content) > _MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            data: dict[str, Any] = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise TypeError("structured response must be object")
            response_id = str(data.get("id") or request.model_request_id)
            model = str(data.get("model") or "fixture-structured-model")
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StructuredModelUnavailable("fixture response unavailable") from exc
        return StructuredModelResponse(parsed, response_id, "fixture-local", model)

    def probe_ready(self) -> bool:
        """Prove the fixed fixture route and protocol are healthy."""
        try:
            if not self._credential_file.read_text(encoding="utf-8").strip():
                return False
            response = httpx.get(_FIXTURE_HEALTH_ENDPOINT, timeout=self._timeout)
            response.raise_for_status()
            return response.json() == {
                "status": "ok",
                "model": "fixture-structured-model",
                "contract": "knowledge-conclusion.fixture.v1",
            }
        except (httpx.HTTPError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

    @property
    def is_available(self) -> bool:
        return self.probe_ready()

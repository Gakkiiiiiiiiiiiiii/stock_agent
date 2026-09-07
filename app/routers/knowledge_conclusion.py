"""Safe HTTP surface for immutable, content-only research conclusions."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.http.content_knowledge_client import ContentBundleClientError
from app.application.knowledge_conclusion.bundle_validator import (
    ContentKnowledgeBundleValidator,
)
from app.application.knowledge_conclusion.lineage import LineageIntegrityError
from app.application.knowledge_conclusion.run_service import ModelReplayRequired
from app.application.knowledge_conclusion.synthesis import (
    KnowledgeConclusionSynthesisService,
)
from app.application.readiness.knowledge_conclusion import (
    KnowledgeConclusionReadiness,
    _fallback_enabled,
)
from app.domain.knowledge_conclusion import (
    KnowledgeConclusionRequest,
    revalidate_public_conclusion,
)
from app.domain.knowledge_conclusion_run import KnowledgeConclusionAuditMetadata
from app.ports.content_knowledge import KnowledgeBundleRequest
from app.ports.knowledge_conclusion_repository import (
    KnowledgeConclusionIdempotencyConflict,
)

router = APIRouter(tags=["knowledge-conclusion"])


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_snapshot_id: str = Field(min_length=1, max_length=256)
    query: str = Field(min_length=1, max_length=4000)
    symbol: str | None = Field(default=None, max_length=64)
    business_as_of: datetime | None = None
    knowledge_as_of: datetime | None = None
    availability_as_of: datetime | None = None


class _ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["VERIFY_HASH", "RECOMPUTE_DETERMINISTIC", "RECOMPUTE_MODEL"]


@dataclass(frozen=True)
class _Trace:
    trace_id: str


def _services():
    from app.dependencies import (
        content_bundle_client,
        knowledge_conclusion_lineage_service,
        knowledge_conclusion_model,
        knowledge_conclusion_run_service,
    )
    return content_bundle_client, knowledge_conclusion_run_service, knowledge_conclusion_lineage_service, knowledge_conclusion_model


def _error(status: int, code: str, message: str, request: Request, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message, "trace_id": request.state.trace_id, "retryable": retryable})


def _map_error(exc: Exception, request: Request) -> JSONResponse:
    code = getattr(exc, "code", str(exc).split(":", 1)[0])
    if isinstance(exc, KnowledgeConclusionIdempotencyConflict):
        return _error(409, "IDEMPOTENCY_CONFLICT", "idempotency key is already bound to a different request", request)
    mapping = {
        "CONTENT_DEPENDENCY_UNAVAILABLE": (503, True), "CONTENT_DEPENDENCY_TIMEOUT": (504, True),
        "CONTENT_CONTRACT_MISMATCH": (503, False), "CONTENT_SCHEMA_INVALID": (422, False),
        "CONTENT_BUNDLE_TAMPERED": (422, False), "CONTENT_SNAPSHOT_MISMATCH": (422, False),
        "CONTENT_AS_OF_VIOLATION": (422, False), "CONTENT_QUALITY_REJECTED": (422, False),
        "MODEL_UNAVAILABLE": (503, True), "MODEL_OUTPUT_INVALID": (422, False),
        "DETERMINISTIC_MODEL_REPLAY_INVALID": (422, False),
        "CONSUMER_BUILD_METADATA_REQUIRED": (503, False),
        "CONCLUSION_UNGROUNDED": (422, False), "CAPABILITY_NOT_ENABLED": (503, False),
        "CONCLUSION_SAFETY_REJECTED": (422, False),
    }
    status, retryable = mapping.get(code, (422, False))
    return _error(status, code if code in mapping else "INVALID_REQUEST", _message(code), request, retryable=retryable)


def _message(code: str) -> str:
    return {
        "CONTENT_BUNDLE_TAMPERED": "content bundle failed integrity validation",
        "CONTENT_DEPENDENCY_UNAVAILABLE": "content dependency is unavailable",
        "CONTENT_DEPENDENCY_TIMEOUT": "content dependency timed out",
        "CONTENT_CONTRACT_MISMATCH": "content contract does not match the locked consumer contract",
    }.get(code, "knowledge conclusion request could not be completed")


def _trace(request: Request) -> _Trace:
    return _Trace(request.state.trace_id)


def _valid_clock(value: datetime | None) -> bool:
    return value is None or (value.tzinfo is not None and value.utcoffset() is not None)


def _consumer_sha() -> str:
    value = os.getenv("AGENT_GIT_COMMIT", "").strip()
    if not value or value.lower() in {"unknown", "none", "null"}:
        raise ValueError("CONSUMER_BUILD_METADATA_REQUIRED")
    return value


@router.post("/api/v2/knowledge-conclusions")
def create_conclusion(
    body: _Request, request: Request, response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> object:
    if "application/json" not in request.headers.get("content-type", "").lower():
        return _error(422, "CONTENT_TYPE_INVALID", "Content-Type must be application/json", request)
    if not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 256:
        return _error(422, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required", request)
    if not all(_valid_clock(value) for value in (body.business_as_of, body.knowledge_as_of, body.availability_as_of)):
        return _error(422, "CONTENT_AS_OF_VIOLATION", "as-of timestamps must be UTC-aware", request)
    if body.content_snapshot_id.strip().casefold() in {"latest", "current", "default"}:
        return _error(422, "CONTENT_SNAPSHOT_MISMATCH", "a concrete content snapshot is required", request)
    try:
        client, runs, _, model = _services()
        raw = KnowledgeConclusionRequest(**body.model_dump())
        metadata = KnowledgeConclusionAuditMetadata(consumer_sha=_consumer_sha(), profile="knowledge-only", trace_id=_trace(request).trace_id)
        run = runs.reserve(request=raw, idempotency_key=idempotency_key.strip(), audit_metadata=metadata)
        if run.result is not None:
            response.status_code = 200
            return _public_result(run.result, request)
        if run.frozen_bundle is None:
            run = runs.request_bundle(run.conclusion_id)
            effective = run.request
            bundle = client.create_bundle(KnowledgeBundleRequest(
                content_snapshot_id=effective.content_snapshot_id, query=effective.query,
                symbol=effective.symbol or "UNSPECIFIED", business_as_of=effective.business_as_of,
                knowledge_as_of=effective.knowledge_as_of, availability_as_of=effective.availability_as_of,
            ), trace=_trace(request))
            runs.freeze_bundle(run.conclusion_id, ContentKnowledgeBundleValidator().freeze_for_conclusion(bundle))
        if not _fallback_enabled() and getattr(model, "is_available", False) is not True:
            return _error(503, "MODEL_UNAVAILABLE", "structured model is unavailable and fallback is disabled", request, retryable=True)
        result = KnowledgeConclusionSynthesisService(runs, model).conclude(run.conclusion_id, worker_id="knowledge-api")
        response.status_code = 201
        return _public_result(result, request)
    except (ContentBundleClientError, KnowledgeConclusionIdempotencyConflict, ValueError, ValidationError) as exc:
        return _map_error(exc, request)


@router.get("/api/v2/knowledge-conclusions/{conclusion_id}")
def get_conclusion(conclusion_id: str, request: Request) -> object:
    _, runs, _, _ = _services()
    run = runs.repository.get(conclusion_id)
    if run is None or run.result is None:
        return _error(404, "CONCLUSION_NOT_FOUND", "knowledge conclusion was not found", request)
    return _public_result(run.result, request)


def _public_result(result: object, request: Request) -> object:
    """Never display a record that no longer satisfies the current text gate."""
    try:
        # The router stores only KnowledgeConclusion values.  Keep this local
        # boundary defensive so old persisted records cannot bypass a newer
        # action-language classifier through GET or idempotent POST.
        return revalidate_public_conclusion(result)  # type: ignore[arg-type]
    except (ValidationError, ValueError, TypeError):
        return _error(422, "CONCLUSION_SAFETY_REJECTED", "stored conclusion failed the current content-only safety gate", request)


@router.post("/api/v2/knowledge-conclusions/{conclusion_id}/replay")
def replay_conclusion(conclusion_id: str, body: _ReplayRequest, request: Request) -> object:
    _, runs, _, _ = _services()
    try:
        value = runs.replay(conclusion_id, mode=body.mode)
        if isinstance(value, ModelReplayRequired):
            return _error(409, "MODEL_REPLAY_REQUIRES_NEW_RUN", "model replay requires a new idempotent run and model identity", request)
        return value
    except KeyError:
        return _error(404, "CONCLUSION_NOT_FOUND", "knowledge conclusion was not found", request)
    except ValueError as exc:
        return _map_error(exc, request)


@router.get("/api/v2/knowledge-conclusions/{conclusion_id}/lineage")
def conclusion_lineage(conclusion_id: str, request: Request) -> object:
    _, _, lineage, _ = _services()
    try:
        return lineage.reconstruct(conclusion_id)
    except KeyError:
        return _error(404, "CONCLUSION_NOT_FOUND", "knowledge conclusion was not found", request)
    except LineageIntegrityError:
        return _error(422, "LINEAGE_INTEGRITY_FAILURE", "frozen conclusion lineage failed integrity validation", request)


@router.get("/health/knowledge-conclusion-ready")
def knowledge_conclusion_ready(response: Response) -> dict[str, object]:
    ready, checks = KnowledgeConclusionReadiness().report()
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "not_ready", "profile": "knowledge-only", "checks": checks}

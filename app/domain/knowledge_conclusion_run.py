"""Durable, content-only conclusion run state.  No HTTP, model, or content client."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.domain.knowledge_conclusion import (
    KnowledgeConclusion,
    KnowledgeConclusionRequest,
)


class KnowledgeConclusionRunState(StrEnum):
    RECEIVED = "RECEIVED"
    BUNDLE_REQUESTED = "BUNDLE_REQUESTED"
    BUNDLE_FROZEN = "BUNDLE_FROZEN"
    SYNTHESIZING = "SYNTHESIZING"
    VALIDATING = "VALIDATING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


_TRANSITIONS = {
    KnowledgeConclusionRunState.RECEIVED: {KnowledgeConclusionRunState.BUNDLE_REQUESTED, KnowledgeConclusionRunState.FAILED},
    KnowledgeConclusionRunState.BUNDLE_REQUESTED: {KnowledgeConclusionRunState.BUNDLE_FROZEN, KnowledgeConclusionRunState.FAILED},
    KnowledgeConclusionRunState.BUNDLE_FROZEN: {KnowledgeConclusionRunState.SYNTHESIZING, KnowledgeConclusionRunState.FAILED},
    KnowledgeConclusionRunState.SYNTHESIZING: {KnowledgeConclusionRunState.VALIDATING, KnowledgeConclusionRunState.FAILED},
    KnowledgeConclusionRunState.VALIDATING: {KnowledgeConclusionRunState.SUCCEEDED, KnowledgeConclusionRunState.FAILED},
    KnowledgeConclusionRunState.SUCCEEDED: set(),
    KnowledgeConclusionRunState.FAILED: set(),
}


class KnowledgeConclusionStateConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class FrozenBundle:
    bundle_id: str
    bundle_hash: str
    payload: dict[str, Any]
    producer_sha: str
    contract_checksum: str
    content_snapshot_id: str


@dataclass(frozen=True)
class KnowledgeConclusionAuditMetadata:
    """Consumer provenance captured with the request, never supplied at read time."""

    consumer_sha: str
    profile: str
    trace_id: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.consumer_sha, self.profile, self.trace_id)):
            raise ValueError("audit metadata values must be nonblank")


@dataclass(frozen=True)
class KnowledgeConclusionCitation:
    finding_index: int
    knowledge_id: str
    evidence_id: str
    quote_hash: str
    quote_hash_provenance: str = "LEGACY_UNSPECIFIED"


@dataclass(frozen=True)
class KnowledgeConclusionRun:
    conclusion_id: str
    idempotency_key: str
    # These are deliberately distinct.  The raw form is the idempotency
    # identity (and retains caller-supplied null clocks); the effective form
    # binds the resolved clocks and every frozen input used by the run.
    raw_request: dict[str, Any]
    raw_request_hash: str
    effective_request_hash: str
    request: KnowledgeConclusionRequest
    policy_version: str
    state: KnowledgeConclusionRunState = KnowledgeConclusionRunState.RECEIVED
    version: int = 0
    frozen_bundle: FrozenBundle | None = None
    model_request_id: str | None = None
    model_provider_idempotency_key: str | None = None
    sealed_model_response: dict[str, Any] | None = None
    result: KnowledgeConclusion | None = None
    result_hash: str | None = None
    error_code: str | None = None
    audit_metadata: KnowledgeConclusionAuditMetadata | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def request_hash(self) -> str:
        """Compatibility alias for the effective, replay/audit request hash."""
        return self.effective_request_hash

    def transition(self, target: KnowledgeConclusionRunState, *, now: datetime, error_code: str | None = None) -> KnowledgeConclusionRun:
        if target not in _TRANSITIONS[self.state]:
            raise KnowledgeConclusionStateConflict(f"INVALID_KNOWLEDGE_CONCLUSION_TRANSITION:{self.state}->{target}")
        return replace(self, state=target, version=self.version + 1, error_code=error_code, updated_at=now)

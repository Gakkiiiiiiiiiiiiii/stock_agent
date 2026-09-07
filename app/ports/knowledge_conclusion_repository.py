"""Persistence port for the isolated knowledge-conclusion run boundary."""
from __future__ import annotations

from collections.abc import Sequence
from threading import RLock
from typing import Protocol
from uuid import uuid4

from app.domain.knowledge_conclusion_lineage import KnowledgeConclusionLineageAudit
from app.domain.knowledge_conclusion_run import (
    KnowledgeConclusionCitation,
    KnowledgeConclusionRun,
    KnowledgeConclusionRunState,
)


class KnowledgeConclusionIdempotencyConflict(ValueError):
    """One idempotency key was supplied with a different canonical request."""


class KnowledgeConclusionFenced(RuntimeError):
    """A stale worker attempted a write after another worker claimed the run."""


# ``None`` remains the backwards-compatible port default meaning "do not
# compare".  A caller which has actually observed that the slot is absent must
# use this value instead.  That distinction is required for a fallback to keep
# an absent response slot fenced across its VALIDATING/result transition.
ABSENT_SEAL_DIGEST = "knowledge-conclusion.sealed-response.absent.v1"


class KnowledgeConclusionRepository(Protocol):
    def reserve(self, run: KnowledgeConclusionRun) -> KnowledgeConclusionRun: ...
    def get(self, conclusion_id: str) -> KnowledgeConclusionRun | None: ...
    def save(self, run: KnowledgeConclusionRun, *, expected_version: int, expected_seal_digest: str | None = None) -> KnowledgeConclusionRun: ...
    def commit_result(self, run: KnowledgeConclusionRun, citations: Sequence[KnowledgeConclusionCitation], *, expected_version: int, expected_seal_digest: str | None = None) -> KnowledgeConclusionRun: ...
    def citations(self, conclusion_id: str) -> tuple[KnowledgeConclusionCitation, ...]: ...
    def audit(self, conclusion_id: str, mode: str, detail: dict[str, object]) -> str: ...
    def audit_events(self, conclusion_id: str) -> tuple[tuple[str, dict[str, object]], ...]: ...
    def append_lineage_audit(self, audit: KnowledgeConclusionLineageAudit) -> KnowledgeConclusionLineageAudit: ...
    def lineage_audits(self, conclusion_id: str) -> tuple[KnowledgeConclusionLineageAudit, ...]: ...


class InMemoryKnowledgeConclusionRepository:
    """Lock-protected reference semantics for unit tests and composition."""
    def __init__(self) -> None:
        self._lock = RLock()
        self._runs: dict[str, KnowledgeConclusionRun] = {}
        self._by_key: dict[str, str] = {}
        self._citations: dict[str, tuple[KnowledgeConclusionCitation, ...]] = {}
        self._audits: list[tuple[str, str, str, dict[str, object]]] = []
        self._lineage_audits: dict[str, list[KnowledgeConclusionLineageAudit]] = {}

    def reserve(self, run: KnowledgeConclusionRun) -> KnowledgeConclusionRun:
        with self._lock:
            prior_id = self._by_key.get(run.idempotency_key)
            if prior_id is not None:
                prior = self._runs[prior_id]
                if prior.raw_request_hash != run.raw_request_hash:
                    raise KnowledgeConclusionIdempotencyConflict("IDEMPOTENCY_KEY_CONFLICT")
                return prior
            self._by_key[run.idempotency_key] = run.conclusion_id
            self._runs[run.conclusion_id] = run
            return run

    def get(self, conclusion_id: str) -> KnowledgeConclusionRun | None:
        with self._lock:
            return self._runs.get(conclusion_id)

    def save(self, run: KnowledgeConclusionRun, *, expected_version: int, expected_seal_digest: str | None = None) -> KnowledgeConclusionRun:
        with self._lock:
            prior = self._runs.get(run.conclusion_id)
            if (
                prior is None
                or prior.version != expected_version
                or (expected_seal_digest is not None and _seal_digest_for_compare(prior) != expected_seal_digest)
            ):
                raise KnowledgeConclusionFenced("KNOWLEDGE_CONCLUSION_RUN_FENCED")
            self._runs[run.conclusion_id] = run
            return run

    def commit_result(self, run: KnowledgeConclusionRun, citations: Sequence[KnowledgeConclusionCitation], *, expected_version: int, expected_seal_digest: str | None = None) -> KnowledgeConclusionRun:
        with self._lock:
            prior = self._runs.get(run.conclusion_id)
            if (
                prior is None
                or prior.version != expected_version
                or (expected_seal_digest is not None and _seal_digest_for_compare(prior) != expected_seal_digest)
            ):
                raise KnowledgeConclusionFenced("KNOWLEDGE_CONCLUSION_RUN_FENCED")
            if prior.state is KnowledgeConclusionRunState.SUCCEEDED:
                return prior
            if len({(row.finding_index, row.knowledge_id, row.evidence_id) for row in citations}) != len(citations):
                raise ValueError("DUPLICATE_KNOWLEDGE_CONCLUSION_CITATION")
            # Assign only after all validation: result and citation effect are one boundary.
            self._runs[run.conclusion_id] = run
            self._citations[run.conclusion_id] = tuple(citations)
            return run

    def citations(self, conclusion_id: str) -> tuple[KnowledgeConclusionCitation, ...]:
        with self._lock:
            return self._citations.get(conclusion_id, ())

    def audit(self, conclusion_id: str, mode: str, detail: dict[str, object]) -> str:
        with self._lock:
            audit_id = str(uuid4())
            self._audits.append((audit_id, conclusion_id, mode, dict(detail)))
            return audit_id

    def audit_events(self, conclusion_id: str) -> tuple[tuple[str, dict[str, object]], ...]:
        with self._lock:
            return tuple((mode, dict(detail)) for _, owner, mode, detail in self._audits if owner == conclusion_id)

    def append_lineage_audit(self, audit: KnowledgeConclusionLineageAudit) -> KnowledgeConclusionLineageAudit:
        with self._lock:
            self._lineage_audits.setdefault(audit.conclusion_id, []).append(audit)
            return audit

    def lineage_audits(self, conclusion_id: str) -> tuple[KnowledgeConclusionLineageAudit, ...]:
        with self._lock:
            return tuple(self._lineage_audits.get(conclusion_id, ()))


def _seal_digest(run: KnowledgeConclusionRun) -> str | None:
    """The compare-and-swap identity for the persisted opaque response."""
    if run.sealed_model_response is None:
        return None
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(run.sealed_model_response, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _seal_digest_for_compare(run: KnowledgeConclusionRun) -> str:
    """Return an explicit identity for both a present and an absent seal."""
    return _seal_digest(run) or ABSENT_SEAL_DIGEST

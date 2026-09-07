"""Frozen-only lineage reconstruction for content-only research conclusions."""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.application.knowledge_conclusion.bundle_validator import (
    BundleValidationError,
    ContentKnowledgeBundleValidator,
)
from app.application.knowledge_conclusion.grounding import bundle_graph, citation_edges
from app.application.knowledge_conclusion.run_service import canonical_hash
from app.domain.knowledge_conclusion import revalidate_public_conclusion
from app.domain.knowledge_conclusion_lineage import (
    KnowledgeConclusionLineage,
    KnowledgeConclusionLineageAudit,
    LineageClaim,
    LineageEvidence,
    LineageFinding,
    LineageOccurrence,
    LineageSourceArtifact,
    LineageTranscript,
)
from app.domain.knowledge_conclusion_run import KnowledgeConclusionRunState
from app.ports.knowledge_conclusion_metrics import KnowledgeConclusionMetrics
from app.ports.knowledge_conclusion_repository import KnowledgeConclusionRepository


class LineageIntegrityError(ValueError):
    """A frozen lineage edge is absent, mis-owned, or does not hash exactly."""


def _records(payload: Mapping[str, Any], *names: str) -> tuple[Mapping[str, Any], ...]:
    for name in names:
        value = payload.get(name)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if not all(isinstance(item, Mapping) for item in value):
                raise LineageIntegrityError(f"invalid {name} records")
            return tuple(value)
    return ()


def _id(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise LineageIntegrityError("lineage identifier missing")


def _links(row: Mapping[str, Any], singular: str, plural: str) -> tuple[str, ...]:
    value = row.get(singular)
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    value = row.get(plural)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and all(isinstance(item, str) and item.strip() for item in value):
        return tuple(item.strip() for item in value)
    return ()


def _safe_public(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 256:
        return None
    lowered = value.casefold()
    if any(token in lowered for token in ("http://", "https://", "secret", "token", "cookie", "authorization", "header", "signed")):
        return None
    return value


def _milliseconds(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise LineageIntegrityError("transcript timestamps must be nonnegative milliseconds")


class KnowledgeConclusionLineageService:
    """Reads only persisted run records; it has no upstream or model dependency."""

    def __init__(self, repository: KnowledgeConclusionRepository, *, metrics: KnowledgeConclusionMetrics | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self.metrics = metrics
        self.clock = clock or (lambda: datetime.now(UTC))

    def reconstruct(self, conclusion_id: str) -> KnowledgeConclusionLineage:
        try:
            run = self.repository.get(conclusion_id)
            if run is None:
                raise KeyError(conclusion_id)
            if run.state is not KnowledgeConclusionRunState.SUCCEEDED or run.frozen_bundle is None or run.result is None or run.result_hash is None:
                raise LineageIntegrityError("conclusion is not a succeeded frozen result")
            if run.audit_metadata is None:
                raise LineageIntegrityError("persisted consumer audit metadata missing")
            try:
                revalidate_public_conclusion(run.result)
            except ValueError as exc:
                raise LineageIntegrityError("stored conclusion failed current content-only safety gate") from exc
            frozen = run.frozen_bundle
            self._validate_frozen_bundle(frozen.payload, frozen.bundle_id, frozen.bundle_hash)
            if frozen.payload.get("content_snapshot_id") != frozen.content_snapshot_id:
                raise LineageIntegrityError("frozen snapshot identifier mismatch")
            if canonical_hash(run.result.model_dump(mode="json")) != run.result_hash:
                raise LineageIntegrityError("committed result hash mismatch")
            if run.result.conclusion_id != run.conclusion_id or run.result.content_bundle_id != frozen.bundle_id:
                raise LineageIntegrityError("result ownership mismatch")
            if run.result.content_snapshot_id != frozen.content_snapshot_id or frozen.content_snapshot_id != run.request.content_snapshot_id:
                raise LineageIntegrityError("snapshot ownership mismatch")

            sources, transcripts, evidence, claims, occurrences = self._bundle_edges(frozen.payload, run.result.findings)
            citations = self._validate_citations(run.conclusion_id, frozen.payload, run.result.findings)
            view = KnowledgeConclusionLineage(
                scope="CONTENT_ONLY_RESEARCH", conclusion_id=run.conclusion_id, snapshot_id=frozen.content_snapshot_id,
                bundle_id=frozen.bundle_id, bundle_hash=frozen.bundle_hash, result_hash=run.result_hash,
                sources=sources, transcripts=transcripts, evidence=evidence, claims=claims, occurrences=occurrences,
                findings=citations,
                producer_sha=frozen.producer_sha, contract_checksum=frozen.contract_checksum,
                consumer_sha=run.audit_metadata.consumer_sha, prompt_version=run.result.model.prompt_version,
                model_mode=run.result.model.mode, model_provider=run.result.model.provider, model_name=run.result.model.model,
                profile=run.audit_metadata.profile, trace_id=run.audit_metadata.trace_id,
            )
            self._append_audit(run, view)
            if self.metrics:
                self.metrics.increment("stock_agent_knowledge_conclusion_total", verdict=run.result.verdict.value,
                                       model_mode=run.result.model.mode, profile=run.audit_metadata.profile,
                                       contract_version=run.result.contract)
            return view
        except LineageIntegrityError:
            if self.metrics:
                self.metrics.increment("stock_agent_content_contract_failure_total", error_code="LINEAGE_INTEGRITY")
            raise

    @staticmethod
    def _validate_frozen_bundle(payload: Mapping[str, Any], bundle_id: str, bundle_hash: str) -> None:
        """Use the locked producer c14n verifier; never substitute Agent JSON c14n."""
        try:
            verified_id, verified_hash = ContentKnowledgeBundleValidator.verify_integrity(payload)
        except BundleValidationError as exc:
            raise LineageIntegrityError("frozen bundle integrity mismatch") from exc
        if verified_id != bundle_id or verified_hash != bundle_hash:
            raise LineageIntegrityError("frozen bundle identifier mismatch")

    def _validate_citations(self, conclusion_id: str, payload: Mapping[str, Any], findings: Sequence[Any]) -> tuple[LineageFinding, ...]:
        stored = self.repository.citations(conclusion_id)
        expected: list[LineageFinding] = []
        for index, finding in enumerate(findings):
            expected.extend(
                LineageFinding(
                    index,
                    edge.knowledge_id,
                    edge.evidence_id,
                    edge.quote_hash,
                    edge.quote_hash_provenance,
                )
                for edge in citation_edges(payload, finding)
            )
        actual = tuple(
            LineageFinding(
                row.finding_index,
                row.knowledge_id,
                row.evidence_id,
                row.quote_hash,
                row.quote_hash_provenance,
            )
            for row in stored
        )
        if len(set(expected)) != len(expected) or len(set(actual)) != len(actual):
            raise LineageIntegrityError("duplicate citation edge")
        if set(actual) != set(expected):
            raise LineageIntegrityError("citation missing, mis-owned, or tampered")
        return tuple(sorted(expected, key=lambda row: (row.finding_index, row.knowledge_id, row.evidence_id)))

    def _bundle_edges(self, payload: Mapping[str, Any], findings: Sequence[Any]) -> tuple[
        tuple[LineageSourceArtifact, ...], tuple[LineageTranscript, ...], tuple[LineageEvidence, ...],
        tuple[LineageClaim, ...], tuple[LineageOccurrence, ...],
    ]:
        source = payload.get("source")
        if not isinstance(source, Mapping):
            raise LineageIntegrityError("frozen source record missing")
        # A Bundle intentionally has no source artifact-id field. Expose a
        # distinctly named derived *node* for its compact source projection;
        # never present its digest as a producer artifact identity.
        source_node_id = "source-node:" + _bundle_digest(source)
        knowledge_graph = {knowledge.knowledge_id: knowledge for knowledge in bundle_graph(payload)}
        graph = {item.evidence_id: item for knowledge in knowledge_graph.values() for item in knowledge.evidence}
        cited_ids = {evidence_id for finding in findings for evidence_id in finding.evidence_ids}
        if not cited_ids <= graph.keys():
            raise LineageIntegrityError("cited evidence missing from frozen graph")
        sources: dict[str, LineageSourceArtifact] = {}
        transcripts: dict[tuple[str, int | None, int | None], LineageTranscript] = {}
        evidence: dict[str, LineageEvidence] = {}
        claims: set[LineageClaim] = set()
        occurrences: set[LineageOccurrence] = set()
        for evidence_id in sorted(cited_ids):
            grounded = graph[evidence_id]
            raw = grounded.raw
            knowledge = knowledge_graph.get(grounded.knowledge_id)
            if knowledge is None:
                raise LineageIntegrityError("evidence knowledge ownership missing")
            claim_id = _id(knowledge.raw, "claim_id")
            occurrence_id = _id(knowledge.raw, "occurrence_id", "claim_occurrence_id")
            start_ms, end_ms = _milliseconds(raw.get("start_ms")), _milliseconds(raw.get("end_ms"))
            if start_ms is not None and end_ms is not None and end_ms < start_ms:
                raise LineageIntegrityError("transcript range is reversed")
            transcript_id = _producer_artifact_id(raw.get("artifact_id"))
            sources[source_node_id] = LineageSourceArtifact(
                source_node_id,
                _safe_public(source.get("source_type") or source.get("kind")),
                _safe_public(source.get("title")),
            )
            # A cited evidence row can be grounded without an upstream
            # transcript artifact. Keep that relationship explicitly
            # unavailable rather than manufacturing a transcript identity.
            if transcript_id is not None:
                transcript = LineageTranscript(transcript_id, source_node_id, start_ms, end_ms)
                transcripts[(transcript_id, start_ms, end_ms)] = transcript
            evidence[evidence_id] = LineageEvidence(evidence_id, grounded.knowledge_id, transcript_id, claim_id, occurrence_id)
            claims.add(LineageClaim(claim_id, evidence_id))
            occurrences.add(LineageOccurrence(occurrence_id, claim_id, evidence_id))
        return (
            tuple(sorted(sources.values(), key=lambda row: row.source_node_id)),
            tuple(sorted(transcripts.values(), key=lambda row: (row.transcript_artifact_id or "", row.start_ms or -1, row.end_ms or -1))),
            tuple(sorted(evidence.values(), key=lambda row: row.evidence_id)),
            tuple(sorted(claims, key=lambda row: (row.claim_id, row.evidence_id))),
            tuple(sorted(occurrences, key=lambda row: (row.occurrence_id, row.claim_id, row.evidence_id))),
        )
    def _append_audit(self, run: Any, view: KnowledgeConclusionLineage) -> None:
        fallback_reason = None
        for mode, detail in self.repository.audit_events(run.conclusion_id):
            if mode == "MODEL_FALLBACK" and isinstance(detail.get("reason"), str):
                fallback_reason = detail["reason"]
        if view.model_mode == "FALLBACK" and not fallback_reason:
            raise LineageIntegrityError("fallback audit reason missing")
        audit_id = str(uuid4())
        lineage_identity_hash = canonical_hash({
            "sources": [asdict(row) for row in view.sources],
            "transcripts": [asdict(row) for row in view.transcripts],
            "evidence": [asdict(row) for row in view.evidence],
            "claims": [asdict(row) for row in view.claims],
            "occurrences": [asdict(row) for row in view.occurrences],
            "citations": [asdict(row) for row in view.findings],
        })
        payload = {
            "audit_id": audit_id, "conclusion_id": view.conclusion_id,
            "raw_request_hash": run.raw_request_hash,
            "effective_request_hash": run.effective_request_hash,
            "bundle_hash": view.bundle_hash,
            "result_hash": view.result_hash, "model_mode": view.model_mode, "model_provider": view.model_provider,
            "model_name": view.model_name, "prompt_version": view.prompt_version, "fallback_reason": fallback_reason,
            "profile": view.profile, "trace_id": view.trace_id, "producer_sha": view.producer_sha,
            "contract_checksum": view.contract_checksum, "consumer_sha": view.consumer_sha,
            "citations": [asdict(row) for row in view.findings], "lineage_identity_hash": lineage_identity_hash,
        }
        audit = KnowledgeConclusionLineageAudit(
            audit_id=audit_id, audit_hash=canonical_hash(payload), recorded_at=self.clock(), citations=view.findings,
            raw_request_hash=run.raw_request_hash, effective_request_hash=run.effective_request_hash,
            bundle_hash=view.bundle_hash, result_hash=view.result_hash,
            model_mode=view.model_mode, model_provider=view.model_provider, model_name=view.model_name,
            prompt_version=view.prompt_version, fallback_reason=fallback_reason, profile=view.profile,
            trace_id=view.trace_id, producer_sha=view.producer_sha, contract_checksum=view.contract_checksum,
            consumer_sha=view.consumer_sha, conclusion_id=view.conclusion_id, lineage_identity_hash=lineage_identity_hash,
        )
        self.repository.append_lineage_audit(audit)


def _bundle_digest(value: Mapping[str, Any]) -> str:
    """Delegate the byte representation to the locked Bundle c14n codec."""
    from app.application.knowledge_conclusion.bundle_validator import canonical_json

    return hashlib.sha256(canonical_json(value)).hexdigest()


def _producer_artifact_id(value: object) -> str | None:
    """Return an explicit, public-safe producer artifact identity if supplied."""
    return _safe_public(value)

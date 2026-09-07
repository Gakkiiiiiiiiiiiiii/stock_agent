"""Public-safe immutable lineage and audit views for content-only conclusions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LineageSourceArtifact:
    """A source projection node, not a claimed producer artifact.

    The locked Content Bundle does not export a source artifact identifier.
    Keeping the separately typed node prevents a deterministic projection hash
    from being presented to callers as an upstream artifact identity.
    """

    source_node_id: str
    source_type: str | None
    title: str | None


@dataclass(frozen=True)
class LineageTranscript:
    transcript_artifact_id: str | None
    source_node_id: str
    start_ms: int | None
    end_ms: int | None


@dataclass(frozen=True)
class LineageEvidence:
    evidence_id: str
    knowledge_id: str
    transcript_artifact_id: str | None
    claim_id: str
    occurrence_id: str


@dataclass(frozen=True)
class LineageClaim:
    claim_id: str
    evidence_id: str


@dataclass(frozen=True)
class LineageOccurrence:
    occurrence_id: str
    claim_id: str
    evidence_id: str


@dataclass(frozen=True)
class LineageFinding:
    finding_index: int
    knowledge_id: str
    evidence_id: str
    quote_hash: str
    quote_hash_provenance: str = "LEGACY_UNSPECIFIED"


@dataclass(frozen=True)
class KnowledgeConclusionLineage:
    scope: str
    conclusion_id: str
    snapshot_id: str
    bundle_id: str
    bundle_hash: str
    result_hash: str
    sources: tuple[LineageSourceArtifact, ...]
    transcripts: tuple[LineageTranscript, ...]
    evidence: tuple[LineageEvidence, ...]
    claims: tuple[LineageClaim, ...]
    occurrences: tuple[LineageOccurrence, ...]
    findings: tuple[LineageFinding, ...]
    producer_sha: str
    contract_checksum: str
    consumer_sha: str
    prompt_version: str
    model_mode: str
    model_provider: str
    model_name: str
    profile: str
    trace_id: str


@dataclass(frozen=True)
class KnowledgeConclusionLineageAudit:
    """Append-only, redacted audit payload. It deliberately stores no evidence text."""

    audit_id: str
    conclusion_id: str
    audit_hash: str
    recorded_at: datetime
    raw_request_hash: str
    effective_request_hash: str
    bundle_hash: str
    result_hash: str
    model_mode: str
    model_provider: str
    model_name: str
    prompt_version: str
    fallback_reason: str | None
    profile: str
    trace_id: str
    producer_sha: str
    contract_checksum: str
    consumer_sha: str
    citations: tuple[LineageFinding, ...]
    lineage_identity_hash: str | None = None

    @property
    def request_hash(self) -> str:
        """Compatibility view: audit/result provenance uses the effective form."""
        return self.effective_request_hash

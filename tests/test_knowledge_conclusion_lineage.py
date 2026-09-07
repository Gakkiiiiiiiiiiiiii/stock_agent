"""SA-09A frozen-only lineage, audit, and low-cardinality metric coverage."""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest
from starlette.requests import Request

from app.application.knowledge_conclusion.bundle_validator import (
    ContentKnowledgeBundleValidator,
    canonical_json,
)
from app.application.knowledge_conclusion.deterministic_fallback import (
    fallback_conclusion,
)
from app.application.knowledge_conclusion.lineage import (
    KnowledgeConclusionLineageService,
    LineageIntegrityError,
)
from app.application.knowledge_conclusion.metrics import (
    InMemoryKnowledgeConclusionMetrics,
    MetricPolicyError,
)
from app.application.knowledge_conclusion.run_service import (
    KnowledgeConclusionRunService,
    canonical_hash,
)
from app.domain.knowledge_conclusion import Finding, KnowledgeConclusionRequest
from app.domain.knowledge_conclusion_run import (
    FrozenBundle,
    KnowledgeConclusionAuditMetadata,
)
from app.ports.knowledge_conclusion_repository import (
    InMemoryKnowledgeConclusionRepository,
)

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _payload() -> dict[str, object]:
    """The producer's locked public source/items/evidence projection."""
    request = {
        "content_snapshot_id": "snapshot-1", "query": "内容支持什么研究结论？", "symbol": "UNSPECIFIED",
        "business_as_of": "2026-09-06T12:00:00Z", "knowledge_as_of": "2026-09-06T12:00:00Z",
        "availability_as_of": "2026-09-06T12:00:00Z", "minimum_support_status": "SOURCE_SUPPORTED",
        "max_items": 20, "policy": "PUBLIC_STRICT", "policy_version": "content-bundle-policy.v1",
    }
    payload: dict[str, object] = {
        "contract": "content-knowledge-bundle.v1", "schema_version": "1.0.0",
        "canonicalization_version": "content-bundle-c14n-v1", "request": request,
        "request_hash": "sha256:" + hashlib.sha256(canonical_json(request)).hexdigest(),
        "content_snapshot_id": "snapshot-1", "query": request["query"],
        "source": {"source_type": "bilibili", "source_identity_hash": "source-1", "source_version_id": "version-1", "canonical_url": "https://example.test/video", "source_content_hash": "content-1"},
        "business_as_of": "2026-09-06T12:00:00Z", "knowledge_as_of": "2026-09-06T12:00:00Z",
        "availability_as_of": "2026-09-06T12:00:00Z",
        "items": [{
            "knowledge_id": "ko-1", "claim_id": "claim-1", "occurrence_id": "occurrence-1",
            "statement": "收入增长12%。", "subject": {"type": "EQUITY", "key": "UNSPECIFIED"},
            "predicate": "revenue_growth", "object": {"value": 12, "unit": "%"}, "temporal": {"target_start": "2026-09-01T00:00:00Z", "target_end": "2026-09-30T00:00:00Z", "precision": "MONTH"},
            "support_status": "SOURCE_SUPPORTED", "lifecycle_status": "ACTIVE", "grounding_status": "GROUNDED",
            "verification": {"status": "VERIFIED", "reason_codes": []}, "evidence": [{
                "evidence_id": "ev-1", "ownership": "PRIMARY", "start_ms": 100, "end_ms": 200,
                "quote": "收入增长12%。", "quote_hash": hashlib.sha256(canonical_json("收入增长12%。")).hexdigest(), "artifact_id": "transcript-artifact-A", "segment_id": "segment-1", "modality": "TRANSCRIPT",
            }],
        }],
        "quality": {"knowledge_count": 1, "grounded_ratio": 1.0, "numeric_grounded_ratio": 1.0, "warnings": []},
        "producer": {"service": "stock_content", "service_version": "fixture", "git_commit": "content-sha", "pipeline_version": "fixture", "contract_checksum": "sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621"},
    }
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    payload["bundle_id"] = "ckb_" + digest
    payload["bundle_hash"] = "sha256:" + digest
    return payload


def _rehash(payload: dict[str, object]) -> None:
    material = {key: value for key, value in payload.items() if key not in {"bundle_id", "bundle_hash"}}
    digest = hashlib.sha256(canonical_json(material)).hexdigest()
    payload["bundle_id"], payload["bundle_hash"] = "ckb_" + digest, "sha256:" + digest


def _ready(*, payload: dict[str, object] | None = None, finding: Finding | None = None):
    repo = InMemoryKnowledgeConclusionRepository()
    run_service = KnowledgeConclusionRunService(repo, clock=lambda: NOW)
    request = KnowledgeConclusionRequest(content_snapshot_id="snapshot-1", query="内容支持什么研究结论？")
    metadata = KnowledgeConclusionAuditMetadata(consumer_sha="agent-sha", profile="knowledge-only", trace_id="trace-1")
    run = run_service.reserve(request=request, idempotency_key="lineage", audit_metadata=metadata)
    data = payload or _payload()
    # The lineage fixture has the current Content producer's source_type and
    # evidence.artifact_id shape and must remain an accepted locked Bundle.
    ContentKnowledgeBundleValidator().validate(data)
    frozen = FrozenBundle(str(data["bundle_id"]), str(data["bundle_hash"]), data, "content-sha", "contract-sha", "snapshot-1")
    run_service.request_bundle(run.conclusion_id)
    run_service.freeze_bundle(run.conclusion_id, frozen)
    run_service.begin_synthesis(run.conclusion_id, worker_id="worker")
    run_service.seal_model_response(run.conclusion_id, sealed_response={"sealed": "fixture"})
    current = repo.get(run.conclusion_id)
    assert current and current.frozen_bundle
    selected = finding or Finding(text="收入增长12%。", knowledge_ids=("ko-1",), evidence_ids=("ev-1",), confidence=0.8)
    result = fallback_conclusion(request=current.request, bundle=current.frozen_bundle.payload, findings=(selected,), content_bundle_id=current.frozen_bundle.bundle_id, conclusion_id=run.conclusion_id, created_at=NOW)
    repo.audit(run.conclusion_id, "MODEL_FALLBACK", {"reason": "MODEL_UNAVAILABLE"})
    run_service.commit_grounded(run.conclusion_id, result=result)
    return repo, run.conclusion_id


def _multi_owner_payload() -> dict[str, object]:
    payload = _payload()
    items = payload["items"]
    assert isinstance(items, list)
    first = items[0]
    assert isinstance(first, dict)
    evidence = first["evidence"]
    assert isinstance(evidence, list)
    evidence.append({
        "evidence_id": "ev-3", "ownership": "PRIMARY", "start_ms": 210, "end_ms": 300,
        "quote": "收入增长12%。", "quote_hash": hashlib.sha256(canonical_json("收入增长12%。")).hexdigest(), "artifact_id": "transcript-artifact-A", "segment_id": "segment-3", "modality": "TRANSCRIPT",
    })
    second = deepcopy(first)
    second["knowledge_id"] = "ko-2"
    second["claim_id"] = "claim-2"
    second["occurrence_id"] = "occurrence-2"
    second_evidence = second["evidence"]
    assert isinstance(second_evidence, list)
    second_evidence[:] = [{
        "evidence_id": "ev-2", "ownership": "PRIMARY", "start_ms": 310, "end_ms": 400,
        "quote": "收入增长12%。", "quote_hash": hashlib.sha256(canonical_json("收入增长12%。")).hexdigest(), "artifact_id": "transcript-artifact-B", "segment_id": "segment-2", "modality": "TRANSCRIPT",
    }]
    items.append(second)
    quality = payload["quality"]
    assert isinstance(quality, dict)
    quality["knowledge_count"] = 2
    _rehash(payload)
    return payload


def test_reconstructs_locked_producer_chain_without_exposing_source_locator() -> None:
    repo, conclusion_id = _ready()
    metrics = InMemoryKnowledgeConclusionMetrics()
    lineage = KnowledgeConclusionLineageService(repo, metrics=metrics, clock=lambda: NOW).reconstruct(conclusion_id)
    assert lineage.scope == "CONTENT_ONLY_RESEARCH"
    assert lineage.sources[0].source_type == "bilibili" and lineage.sources[0].title is None
    assert lineage.sources[0].source_node_id.startswith("source-node:")
    assert lineage.transcripts[0].transcript_artifact_id == "transcript-artifact-A"
    assert lineage.transcripts[0].start_ms == 100 and lineage.evidence[0].claim_id == "claim-1"
    assert lineage.claims[0].evidence_id == "ev-1" and lineage.occurrences[0].claim_id == "claim-1"
    assert lineage.findings[0].quote_hash == canonical_hash("收入增长12%。")
    public = str(asdict(lineage))
    assert "收入增长12" not in public and "https://" not in public
    assert repo.lineage_audits(conclusion_id)[0].fallback_reason == "MODEL_UNAVAILABLE"
    assert metrics.snapshot()[0].labels == {"verdict": "SUPPORTED", "model_mode": "FALLBACK", "profile": "knowledge-only", "contract_version": "knowledge-conclusion.v1"}


def test_multi_owner_citations_preserve_only_producer_pairs_and_all_lineage_edges() -> None:
    payload = _multi_owner_payload()
    finding = Finding(
        text="收入增长12%。",
        knowledge_ids=("ko-1", "ko-2"),
        evidence_ids=("ev-1", "ev-2", "ev-3"),
        confidence=0.8,
    )
    repo, conclusion_id = _ready(payload=payload, finding=finding)

    # Three real producer ownership edges; the prior implementation emitted
    # six cross-product rows and silently collapsed claim/occurrence edges.
    stored = repo.citations(conclusion_id)
    assert {(row.knowledge_id, row.evidence_id) for row in stored} == {
        ("ko-1", "ev-1"), ("ko-1", "ev-3"), ("ko-2", "ev-2"),
    }
    assert len(stored) == 3
    assert {row.quote_hash for row in stored} == {canonical_hash("收入增长12%。")}
    assert {row.quote_hash_provenance for row in stored} == {"PRODUCER_EXPLICIT"}

    lineage = KnowledgeConclusionLineageService(repo, clock=lambda: NOW).reconstruct(conclusion_id)
    assert {(row.knowledge_id, row.evidence_id) for row in lineage.findings} == {
        ("ko-1", "ev-1"), ("ko-1", "ev-3"), ("ko-2", "ev-2"),
    }
    assert {(row.claim_id, row.evidence_id) for row in lineage.claims} == {
        ("claim-1", "ev-1"), ("claim-1", "ev-3"), ("claim-2", "ev-2"),
    }
    assert {(row.occurrence_id, row.evidence_id) for row in lineage.occurrences} == {
        ("occurrence-1", "ev-1"), ("occurrence-1", "ev-3"), ("occurrence-2", "ev-2"),
    }
    audit = repo.lineage_audits(conclusion_id)[0]
    assert audit.citations == lineage.findings
    assert audit.lineage_identity_hash


def test_explicit_producer_quote_hash_is_verified_and_persisted_with_provenance() -> None:
    payload = _payload()
    items = payload["items"]
    assert isinstance(items, list) and isinstance(items[0], dict)
    evidence = items[0]["evidence"]
    assert isinstance(evidence, list) and isinstance(evidence[0], dict)
    supplied = "sha256:" + hashlib.sha256(canonical_json("收入增长12%。")).hexdigest()
    evidence[0]["quote_hash"] = supplied
    _rehash(payload)

    repo, conclusion_id = _ready(payload=payload)

    citation = repo.citations(conclusion_id)[0]
    assert citation.quote_hash == supplied
    assert citation.quote_hash_provenance == "PRODUCER_EXPLICIT"
    reconstructed = KnowledgeConclusionLineageService(repo, clock=lambda: NOW).reconstruct(conclusion_id).findings[0]
    assert (reconstructed.quote_hash, reconstructed.quote_hash_provenance) == (supplied, "PRODUCER_EXPLICIT")


def test_cross_owner_and_tampered_producer_quote_hash_fail_closed() -> None:
    payload = _multi_owner_payload()
    with pytest.raises(ValueError, match="CITATION_INVALID"):
        _ready(payload=payload, finding=Finding(
            text="收入增长12%。", knowledge_ids=("ko-1",), evidence_ids=("ev-2",), confidence=0.8,
        ))

    payload = _multi_owner_payload()
    items = payload["items"]
    assert isinstance(items, list) and isinstance(items[0], dict)
    evidence = items[0]["evidence"]
    assert isinstance(evidence, list) and isinstance(evidence[0], dict)
    evidence[0]["quote_hash"] = "0" * 64
    _rehash(payload)
    with pytest.raises(ValueError, match="producer quote hash"):
        _ready(payload=payload)


def test_locked_producer_artifact_ids_do_not_collapse_and_are_bound_into_audit() -> None:
    first_payload = _payload()
    second_payload = _payload()
    second_evidence = second_payload["items"][0]["evidence"][0]
    second_evidence["artifact_id"] = "transcript-artifact-B"
    material = {key: value for key, value in second_payload.items() if key not in {"bundle_id", "bundle_hash"}}
    digest = hashlib.sha256(canonical_json(material)).hexdigest()
    second_payload["bundle_id"], second_payload["bundle_hash"] = "ckb_" + digest, "sha256:" + digest

    first_repo, first_id = _ready(payload=first_payload)
    second_repo, second_id = _ready(payload=second_payload)
    first = KnowledgeConclusionLineageService(first_repo, clock=lambda: NOW).reconstruct(first_id)
    second = KnowledgeConclusionLineageService(second_repo, clock=lambda: NOW).reconstruct(second_id)

    assert first.bundle_hash != second.bundle_hash
    assert first.transcripts[0].transcript_artifact_id == "transcript-artifact-A"
    assert second.transcripts[0].transcript_artifact_id == "transcript-artifact-B"
    assert first.evidence[0].transcript_artifact_id != second.evidence[0].transcript_artifact_id
    assert first_repo.lineage_audits(first_id)[0].lineage_identity_hash != second_repo.lineage_audits(second_id)[0].lineage_identity_hash


def test_missing_producer_artifact_is_rejected_by_the_locked_bundle_schema() -> None:
    payload = _payload()
    del payload["items"][0]["evidence"][0]["artifact_id"]
    material = {key: value for key, value in payload.items() if key not in {"bundle_id", "bundle_hash"}}
    digest = hashlib.sha256(canonical_json(material)).hexdigest()
    payload["bundle_id"], payload["bundle_hash"] = "ckb_" + digest, "sha256:" + digest
    with pytest.raises(ValueError, match="CONTENT_SCHEMA_INVALID"):
        _ready(payload=payload)


def test_lineage_route_returns_locked_producer_artifact_and_appends_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import knowledge_conclusion as router

    repo, conclusion_id = _ready()
    service = KnowledgeConclusionLineageService(repo, clock=lambda: NOW)
    monkeypatch.setattr(router, "_services", lambda: (object(), object(), service, object()))
    request = Request({"type": "http", "method": "GET", "path": f"/api/v2/knowledge-conclusions/{conclusion_id}/lineage", "headers": []})
    request.state.trace_id = "trace-lineage-route"

    response = router.conclusion_lineage(conclusion_id, request)

    assert response.transcripts[0].transcript_artifact_id == "transcript-artifact-A"
    assert response.sources[0].source_node_id.startswith("source-node:")
    assert len(repo.lineage_audits(conclusion_id)) == 1


@pytest.mark.parametrize("mutation", ("missing", "misowned", "quote"))
def test_missing_misowned_and_tampered_citations_fail_closed(mutation: str) -> None:
    repo, conclusion_id = _ready()
    citations = repo._citations[conclusion_id]  # fixture-only adversarial persistence mutation
    if mutation == "missing":
        repo._citations[conclusion_id] = ()
    elif mutation == "misowned":
        repo._citations[conclusion_id] = (replace(citations[0], knowledge_id="ko-other"),)
    else:
        repo._citations[conclusion_id] = (replace(citations[0], quote_hash="0" * 64),)
    with pytest.raises(LineageIntegrityError, match="citation"):
        KnowledgeConclusionLineageService(repo).reconstruct(conclusion_id)


def test_lineage_is_frozen_only_and_audits_are_append_only_under_concurrent_reads() -> None:
    repo, conclusion_id = _ready()
    service = KnowledgeConclusionLineageService(repo, clock=lambda: NOW)
    with ThreadPoolExecutor(max_workers=8) as pool:
        views = list(pool.map(lambda _: service.reconstruct(conclusion_id), range(16)))
    assert {view.bundle_hash for view in views} == {_payload()["bundle_hash"]}
    assert len(repo.lineage_audits(conclusion_id)) == 16


def test_metric_policy_rejects_high_cardinality_or_secret_labels_and_bounds_series() -> None:
    metrics = InMemoryKnowledgeConclusionMetrics(max_series=2)
    metrics.increment("stock_agent_knowledge_conclusion_total", verdict="SUPPORTED", model_mode="FALLBACK", profile="knowledge-only", contract_version="knowledge-conclusion.v1")
    with pytest.raises(MetricPolicyError):
        metrics.increment("stock_agent_knowledge_conclusion_total", query="content query")
    with pytest.raises(MetricPolicyError):
        metrics.increment("stock_agent_knowledge_conclusion_total", error_code="https://secret.invalid")
    for code in ("ONE", "TWO", "THREE"):
        metrics.increment("stock_agent_content_contract_failure_total", error_code=code)
    assert len(metrics.snapshot()) == 2

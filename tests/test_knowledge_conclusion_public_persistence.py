"""Public conclusion-route persistence proof using the repository SQL harness.

This is deliberately *not* labelled a live PostgreSQL result: ``isolated_database``
uses the repository migration harness with SQLite.  The test reaches the public
FastAPI routes and the concrete persistence adapter, and reads rows only after
the route completed.  A deployed PostgreSQL/API proof remains the integration
flow's responsibility.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.local.unavailable_structured_model import UnavailableStructuredModel
from app.adapters.postgres.knowledge_conclusion_repository import (
    PostgresKnowledgeConclusionRepository,
)
from app.application.knowledge_conclusion.bundle_validator import (
    ContentKnowledgeBundleValidator,
    canonical_json,
)
from app.application.knowledge_conclusion.lineage import (
    KnowledgeConclusionLineageService,
)
from app.application.knowledge_conclusion.run_service import (
    KnowledgeConclusionRunService,
)
from app.application.readiness.knowledge_conclusion import _fallback_enabled
from app.ports.content_knowledge import (
    CONTENT_KNOWLEDGE_CONTRACT,
    CONTENT_KNOWLEDGE_V2_CONTRACT,
    CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM,
    ContentKnowledgeBundle,
    KnowledgeBundleRequest,
)
from app.routers import knowledge_conclusion as router

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _v2_payload(request: KnowledgeBundleRequest) -> dict[str, Any]:
    """Small owned multimodal vector; no route path inserts persistence rows."""
    validator = ContentKnowledgeBundleValidator()
    request_payload = validator.request_payload(request)
    statement = "单一标的仓位不超过20%。"
    payload: dict[str, Any] = {
        "contract": CONTENT_KNOWLEDGE_V2_CONTRACT,
        "schema_version": "2.0.0",
        "canonicalization_version": "content-bundle-c14n-v2",
        "request": request_payload,
        "request_hash": "",
        "content_snapshot_id": "snapshot-v2-public",
        "query": "小鹅通课程中有哪些可验证的风控知识？",
        "scope": {"subject_scope": "ALL_SUBJECTS", "requested_subject": None},
        "source": {
            "source_type": "xiaoe", "source_identity_hash": "source-v2-public",
            "source_version_id": "version-v2-public", "canonical_url": "https://example.com/course",
            "source_content_hash": "source-content-v2-public",
        },
        "business_as_of": "2026-09-09T00:00:00Z",
        "knowledge_as_of": "2026-09-09T00:00:00Z",
        "availability_as_of": "2026-09-09T00:00:00Z",
        "items": [{
            "knowledge_id": "ku-v2-public", "claim_id": "claim-v2-public", "occurrence_id": "occ-v2-public",
            "statement": statement,
            "subject": {"type": "DOMAIN", "key": "PORTFOLIO_RISK_MANAGEMENT"},
            "predicate": "position_limit", "object": {"value": 20, "unit": "PERCENT"},
            "primary_domain": "PORTFOLIO_RISK_MANAGEMENT", "claim_nature": "METHOD",
            "attribution": {"attributed": False, "source_label": None, "speaker": None},
            "source_grade": "PRIMARY",
            "detail": {
                "explanation": "集中度上限限制单一标的风险。", "procedure": "建仓前按总资产计算仓位。",
                "mechanism": None, "formula": None, "example": None, "scope": None,
                "risks": "超过上限会放大单一标的损失。",
            },
            "temporal": {"kind": "RECURRING_RULE", "start": None, "end": None, "as_of": None,
                         "rule": "建仓前", "label": None, "precision": "RULE", "explicitly_unknown": False},
            "evidence": [
                {"evidence_id": "ev-v2-transcript", "ownership": "PRIMARY", "modality": "transcript",
                 "artifact_id": "asr-v2-public", "artifact_hash": "sha256:" + "a" * 64,
                 "locator": {"segment_id": "seg-v2-public", "frame_id": None, "start_ms": 1000, "end_ms": 3000, "bbox": None},
                 "content": statement},
                {"evidence_id": "ev-v2-ocr", "ownership": "PRIMARY", "modality": "ocr",
                 "artifact_id": "frame-v2-public", "artifact_hash": "sha256:" + "b" * 64,
                 "locator": {"segment_id": "seg-v2-public", "frame_id": "frame-v2-public", "start_ms": 1000, "end_ms": 3000, "bbox": [0, 0, 100, 40]},
                 "content": "单一标的≤20%", "model": {"name": "PaddleOCR", "version": "3.7", "confidence": 0.99}},
            ],
            "occurrence_review": {"status": "NOT_REQUIRED", "reason_codes": []},
            "support_status": "CROSS_MODAL_SUPPORTED", "lifecycle_status": "ACTIVE",
            "verification": {"status": "VERIFIED", "reason_codes": []}, "external_truth_status": "EXTERNALLY_VERIFIED", "grounding_status": "GROUNDED", "confidence": 0.9,
        }],
        "quality": {"candidate_count": 1, "eligible_candidate_count": 1, "excluded_candidate_count": 0, "truncated_candidate_count": 0, "knowledge_count": 1, "grounded_count": 1,
                    "numeric_candidate_count": 1, "numeric_grounded_count": 1,
                    "human_review_required_count": 0, "conflict_count": 0, "secondary_only_count": 0, "external_truth_not_checked_count": 0,
                    "grounded_ratio": 1.0, "numeric_grounded_ratio": 1.0, "warnings": []},
        "producer": {"service": "stock_content", "service_version": "2", "git_commit": "a" * 40,
                     "pipeline_version": "pipeline.v4", "contract_checksum": CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM},
    }
    payload["request_hash"] = "sha256:" + hashlib.sha256(canonical_json(request_payload)).hexdigest()
    material = {key: value for key, value in payload.items() if key not in {"bundle_id", "bundle_hash"}}
    digest = hashlib.sha256(canonical_json(material)).hexdigest()
    payload["bundle_id"] = "ckb_" + digest
    payload["bundle_hash"] = "sha256:" + digest
    return payload


def test_public_http_v2_bundle_is_frozen_persisted_and_replayed_without_refetch(
    isolated_database, monkeypatch
) -> None:
    """Route -> concrete SQL repository -> GET/lineage/replay, all from frozen bytes."""
    monkeypatch.setenv("AGENT_GIT_COMMIT", "agent-public-persistence-test")
    # First call freezes the validated v2 bytes, then stops at the explicit
    # model readiness gate.  The identical retry must resume that durable run
    # rather than fetching Content a second time.
    monkeypatch.setenv("KNOWLEDGE_CONCLUSION_DETERMINISTIC_FALLBACK", "false")
    assert _fallback_enabled() is False
    repository = PostgresKnowledgeConclusionRepository()
    runs = KnowledgeConclusionRunService(repository, clock=lambda: NOW)
    lineage = KnowledgeConclusionLineageService(repository, clock=lambda: NOW)
    received: list[KnowledgeBundleRequest] = []

    class Content:
        calls = 0

        def create_bundle(self, request: KnowledgeBundleRequest, *, trace: object) -> ContentKnowledgeBundle:
            self.calls += 1
            received.append(request)
            payload = _v2_payload(request)
            return ContentKnowledgeBundleValidator().validate(payload, expected=request)

    content = Content()

    monkeypatch.setattr(router, "_services", lambda: (content, runs, lineage, UnavailableStructuredModel()))
    app = FastAPI()

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-public-persistence"
        return await call_next(request)

    app.include_router(router.router)
    client = TestClient(app)
    body = {
        "content_snapshot_id": "snapshot-v2-public", "query": "小鹅通课程中有哪些可验证的风控知识？",
        "business_as_of": "2026-09-09T00:00:00Z", "knowledge_as_of": "2026-09-09T00:00:00Z",
        "availability_as_of": "2026-09-09T00:00:00Z", "content_bundle_contract": CONTENT_KNOWLEDGE_V2_CONTRACT,
    }
    headers = {"Content-Type": "application/json", "Idempotency-Key": "public-v2-persistence-key"}
    unavailable = client.post("/api/v2/knowledge-conclusions", json=body, headers=headers)
    assert unavailable.status_code == 503 and unavailable.json()["code"] == "MODEL_UNAVAILABLE"
    # The next POST is the externally observable recovery proof; it has
    # exactly one Content call even though the first request returned 503.
    assert content.calls == 1
    monkeypatch.setenv("KNOWLEDGE_CONCLUSION_DETERMINISTIC_FALLBACK", "true")
    assert _fallback_enabled() is True
    created = client.post("/api/v2/knowledge-conclusions", json=body, headers=headers)
    assert created.status_code == 201, created.text
    conclusion_id = created.json()["conclusion_id"]
    assert created.json()["scope"] == "CONTENT_ONLY_RESEARCH"
    assert created.json()["execution_eligible"] is False
    assert received[0].contract_version == CONTENT_KNOWLEDGE_V2_CONTRACT

    duplicate = client.post("/api/v2/knowledge-conclusions", json=body, headers=headers)
    # The bundle contract selects a different upstream schema/canonicalizer.
    # It must therefore be part of the caller identity even when every other
    # body field and the Idempotency-Key are identical.
    contract_conflict = client.post(
        "/api/v2/knowledge-conclusions",
        json={**body, "content_bundle_contract": CONTENT_KNOWLEDGE_CONTRACT},
        headers=headers,
    )
    conflict = client.post("/api/v2/knowledge-conclusions", json={**body, "query": body["query"] + "不同"}, headers=headers)
    assert duplicate.status_code == 200 and duplicate.json()["conclusion_id"] == conclusion_id
    assert contract_conflict.status_code == 409 and contract_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert content.calls == 1

    stored = client.get(f"/api/v2/knowledge-conclusions/{conclusion_id}")
    lineage_view = client.get(f"/api/v2/knowledge-conclusions/{conclusion_id}/lineage")
    verified = client.post(f"/api/v2/knowledge-conclusions/{conclusion_id}/replay", json={"mode": "VERIFY_HASH"})
    replayed = client.post(f"/api/v2/knowledge-conclusions/{conclusion_id}/replay", json={"mode": "RECOMPUTE_DETERMINISTIC"})
    assert stored.status_code == lineage_view.status_code == verified.status_code == replayed.status_code == 200
    assert verified.json()["valid"] is True
    assert replayed.json()["result"] == created.json()
    assert content.calls == 1  # VERIFY_HASH/RECOMPUTE use frozen persisted Bundle only.

    # Read-only database evidence after the public route; this test performs
    # no direct final-table insertion.
    with isolated_database.connect() as connection:
        run = connection.execute(text("SELECT state, frozen_bundle_id, frozen_bundle_hash, result_hash, raw_request_json FROM knowledge_conclusion_run WHERE conclusion_id=:id"), {"id": conclusion_id}).mappings().one()
        citations = connection.execute(text("SELECT knowledge_id, evidence_id FROM knowledge_conclusion_citation WHERE conclusion_id=:id ORDER BY evidence_id"), {"id": conclusion_id}).mappings().all()
        replay_audits = connection.execute(text("SELECT mode FROM knowledge_conclusion_replay_audit WHERE conclusion_id=:id"), {"id": conclusion_id}).scalars().all()
        lineage_audits = connection.execute(text("SELECT audit_hash FROM knowledge_conclusion_lineage_audit WHERE conclusion_id=:id"), {"id": conclusion_id}).scalars().all()
    assert run["state"] == "SUCCEEDED" and run["frozen_bundle_id"].startswith("ckb_") and run["frozen_bundle_hash"].startswith("sha256:") and run["result_hash"]
    assert '"content_bundle_contract":"content-knowledge-bundle.v2"' in run["raw_request_json"]
    assert {(row["knowledge_id"], row["evidence_id"]) for row in citations} == {("ku-v2-public", "ev-v2-transcript"), ("ku-v2-public", "ev-v2-ocr")}
    assert {"VERIFY_HASH", "RECOMPUTE_DETERMINISTIC"} <= set(replay_audits)
    assert lineage_audits

"""Cross-owner boundary probe for the immutable Content SQL Bundle.

The producer runs in a child process so this consumer neither imports nor
couples itself to Content implementation modules.  The child uses Content's
production SQL authority/repository rather than a hand-built JSON fixture.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.adapters.local.unavailable_structured_model import UnavailableStructuredModel
from app.application.knowledge_conclusion.bundle_validator import (
    ContentKnowledgeBundleValidator,
)
from app.application.knowledge_conclusion.lineage import (
    KnowledgeConclusionLineageService,
)
from app.application.knowledge_conclusion.run_service import (
    KnowledgeConclusionRunService,
)
from app.ports.content_knowledge import KnowledgeBundleRequest
from app.ports.knowledge_conclusion_repository import (
    InMemoryKnowledgeConclusionRepository,
)
from app.routers import knowledge_conclusion as conclusion_router

CONTENT_ROOT = Path(r"D:\project\worktrees\stock_content-EPIC-043")
NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _production_sql_bundle(tmp_path: Path) -> dict[str, object]:
    """Serialize a real SQL-authority Bundle in Content's own process."""
    scratch = tmp_path / "content-sql"
    scratch.mkdir()
    program = """
import json, runpy, sys
from dataclasses import replace
from pathlib import Path
root, scratch = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root / 'src'))
parts = runpy.run_path(str(root / 'tests' / 'test_knowledge_bundle_sql_authority.py'))
database, _ = parts['_authority_with_snapshot'](scratch)
from stock_content.adapters.postgres.repositories.knowledge_bundle_repository import PostgresKnowledgeBundleAuthority, PostgresKnowledgeBundleRepository
from stock_content.application.knowledge_bundle_service import BundleProducerMetadata, KnowledgeBundleService
service = KnowledgeBundleService(
    PostgresKnowledgeBundleAuthority(database.session_factory),
    PostgresKnowledgeBundleRepository(database.session_factory),
    BundleProducerMetadata('stock_content', 'test', 'content-test-sha', 'pipeline-test', 'sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621'),
)
print(json.dumps(service.create(replace(parts['_request'](), max_items=20)), ensure_ascii=False))
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(CONTENT_ROOT), str(scratch)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


class _ContentClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload, self.calls = payload, 0

    def create_bundle(self, request: KnowledgeBundleRequest, *, trace: object):
        self.calls += 1
        return ContentKnowledgeBundleValidator().validate(self.payload, expected=request)


def _client(monkeypatch, payload: dict[str, object]) -> tuple[TestClient, _ContentClient, InMemoryKnowledgeConclusionRepository]:
    content = _ContentClient(payload)
    repository = InMemoryKnowledgeConclusionRepository()
    runs = KnowledgeConclusionRunService(repository, clock=lambda: NOW)
    lineage = KnowledgeConclusionLineageService(repository, clock=lambda: NOW)
    monkeypatch.setenv("AGENT_GIT_COMMIT", "candidate-agent-base-sha")
    monkeypatch.setenv("KNOWLEDGE_CONCLUSION_DETERMINISTIC_FALLBACK", "true")
    monkeypatch.setattr(
        conclusion_router,
        "_services",
        lambda: (content, runs, lineage, UnavailableStructuredModel()),
    )
    app = FastAPI()

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-content-sql-agent"
        return await call_next(request)

    app.include_router(conclusion_router.router)
    return TestClient(app), content, repository


def test_real_content_sql_bundle_crosses_agent_routes_and_replays_without_refetch(tmp_path: Path, monkeypatch) -> None:
    payload = _production_sql_bundle(tmp_path)
    client, content, repository = _client(monkeypatch, payload)
    request = {
        "content_snapshot_id": "snapshot-1", "query": "收入", "symbol": "600000",
    }
    headers = {"Idempotency-Key": "content-sql-routes", "Content-Type": "application/json"}
    created = client.post("/api/v2/knowledge-conclusions", json=request, headers=headers)
    assert created.status_code == 201, created.text
    conclusion = created.json()
    conclusion_id = conclusion["conclusion_id"]
    assert conclusion["execution_eligible"] is False
    assert content.calls == 1

    stored = client.get(f"/api/v2/knowledge-conclusions/{conclusion_id}")
    assert stored.status_code == 200 and stored.json()["conclusion_id"] == conclusion_id
    verified = client.post(f"/api/v2/knowledge-conclusions/{conclusion_id}/replay", json={"mode": "VERIFY_HASH"})
    assert verified.status_code == 200 and verified.json()["valid"] is True
    replayed = client.post(f"/api/v2/knowledge-conclusions/{conclusion_id}/replay", json={"mode": "RECOMPUTE_DETERMINISTIC"})
    assert replayed.status_code == 200 and replayed.json()["result"]["conclusion_id"] == conclusion_id
    lineage = client.get(f"/api/v2/knowledge-conclusions/{conclusion_id}/lineage")
    assert lineage.status_code == 200
    assert content.calls == 1, "GET, replay, and lineage must use the frozen Bundle"

    run = repository.get(conclusion_id)
    assert run is not None and run.raw_request_hash != run.effective_request_hash
    assert run.frozen_bundle is not None
    assert run.frozen_bundle.payload["producer"]["contract_checksum"] == "sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621"
    citations = repository.citations(conclusion_id)
    assert citations and citations[0].evidence_id == "evidence-1"
    assert {mode for mode, _ in repository.audit_events(conclusion_id)} >= {"MODEL_FALLBACK", "VERIFY_HASH", "RECOMPUTE_DETERMINISTIC"}
    assert repository.lineage_audits(conclusion_id)


def test_real_content_sql_bundle_rejects_old_contract_before_agent_route(tmp_path: Path) -> None:
    payload = _production_sql_bundle(tmp_path)
    producer = payload["producer"]
    assert isinstance(producer, dict)
    producer["contract_checksum"] = "sha256:CC9C3D52602D83A7D77800F4E18162D2066D727E27D3925A0A8789181929D198"
    # The checksum is part of the immutable material; changing it cannot be
    # repaired by reusing the old identity.
    try:
        ContentKnowledgeBundleValidator().validate(payload)
    except ValueError as error:
        assert str(error) in {"CONTENT_BUNDLE_TAMPERED", "CONTENT_CONTRACT_MISMATCH"}
    else:  # pragma: no cover - documents the cross-owner fail-closed rule
        raise AssertionError("old producer contract unexpectedly accepted")

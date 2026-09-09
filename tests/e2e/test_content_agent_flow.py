"""Local-only protocol tests for the deterministic Content--Agent E2E driver."""
from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from app.application.knowledge_conclusion.bundle_validator import (
    BundleValidationError,
    ContentKnowledgeBundleValidator,
    canonical_json,
)
from app.application.knowledge_conclusion.grounding import GroundingError
from app.application.knowledge_conclusion.run_service import canonical_hash
from scripts.e2e.common import E2EError, prepare_evidence_dir
from scripts.e2e.run_content_agent_flow import parser, run
from scripts.e2e.scan_secrets import scan
from scripts.e2e.verify_knowledge_bundle import verify as verify_bundle
from scripts.e2e.verify_knowledge_conclusion import verify as verify_conclusion

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def _bundle() -> dict[str, Any]:
    validator = ContentKnowledgeBundleValidator()
    request = validator.request_payload(type("Request", (), {"content_snapshot_id": "snapshot-1", "query": "核心逻辑、条件和风险是什么？", "symbol": "UNSPECIFIED", "business_as_of": NOW, "knowledge_as_of": NOW, "availability_as_of": NOW, "minimum_support_status": "SOURCE_SUPPORTED", "max_items": 20, "policy": "PUBLIC_STRICT", "policy_version": "content-bundle-policy.v1"})())
    payload: dict[str, Any] = {
        "contract": "content-knowledge-bundle.v1", "schema_version": "1.0.0", "canonicalization_version": "content-bundle-c14n-v1",
        "request": request, "request_hash": validator.request_hash(request), "content_snapshot_id": "snapshot-1", "query": request["query"],
        "source": {"source_type": "bilibili", "source_identity_hash": "source-1", "source_version_id": "version-1", "canonical_url": "https://example.test/video", "source_content_hash": "content-1"},
        "business_as_of": "2026-09-06T00:00:00Z", "knowledge_as_of": "2026-09-06T00:00:00Z", "availability_as_of": "2026-09-06T00:00:00Z",
        "items": [{"knowledge_id": "k-1", "claim_id": "claim-1", "occurrence_id": "occurrence-1", "statement": "收入增长12%。", "subject": {"type": "EQUITY", "key": "UNSPECIFIED"}, "predicate": "revenue_growth", "object": {"value": 12, "unit": "%"}, "temporal": {"target_start": "2026-09-01T00:00:00Z", "target_end": "2026-09-30T00:00:00Z", "precision": "MONTH"}, "support_status": "SOURCE_SUPPORTED", "lifecycle_status": "ACTIVE", "grounding_status": "GROUNDED", "verification": {"status": "VERIFIED", "reason_codes": []}, "evidence": [{"evidence_id": "e-1", "ownership": "PRIMARY", "artifact_id": "artifact-1", "segment_id": "segment-1", "start_ms": 1, "end_ms": 2, "quote": "收入增长12%。", "quote_hash": "quote-hash", "modality": "TRANSCRIPT"}]}],
        "quality": {"knowledge_count": 1, "grounded_ratio": 1.0, "numeric_grounded_ratio": 1.0, "warnings": []},
        "producer": {"service": "stock_content", "service_version": "1", "git_commit": "producer-sha", "pipeline_version": "fixture", "contract_checksum": "sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621"},
    }
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    payload["bundle_id"], payload["bundle_hash"] = "ckb_" + digest, "sha256:" + digest
    return payload


def _conclusion(bundle: dict[str, Any]) -> dict[str, Any]:
    return {"contract": "knowledge-conclusion.v1", "conclusion_id": "conclusion-1", "scope": "CONTENT_ONLY_RESEARCH", "execution_eligible": False,
            "query": bundle["query"], "content_bundle_id": bundle["bundle_id"], "content_snapshot_id": bundle["content_snapshot_id"],
            "verdict": "SUPPORTED", "market_stance": "NEUTRAL", "summary": "收入增长12。",
            "findings": [{"text": "收入增长12。", "knowledge_ids": ["k-1"], "evidence_ids": ["e-1"], "confidence": 0.9}],
            "conditions": [], "risks": [], "contradictions": [], "limitations": [],
            "model": {"mode": "FALLBACK", "provider": "deterministic", "model": "fixture", "prompt_version": "knowledge-conclusion.prompt.v1"}, "created_at": "2026-09-06T00:00:00Z"}


def _bundle_for_snapshot(bundle: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
    candidate = deepcopy(bundle)
    candidate["content_snapshot_id"] = snapshot_id
    candidate["request"]["content_snapshot_id"] = snapshot_id
    validator = ContentKnowledgeBundleValidator()
    candidate["request_hash"] = validator.request_hash(candidate["request"])
    material = {key: value for key, value in candidate.items() if key not in {"bundle_id", "bundle_hash"}}
    digest = hashlib.sha256(canonical_json(material)).hexdigest()
    candidate["bundle_id"], candidate["bundle_hash"] = "ckb_" + digest, "sha256:" + digest
    return candidate


class _FixtureServer:
    def __init__(self, bundle: dict[str, Any], conclusion: dict[str, Any]) -> None:
        self.bundle, self.conclusion, self.content_bundle_calls = bundle, conclusion, 0
        self.active_bundle, self.active_conclusion = bundle, conclusion
        self.candidate_snapshot_id = "snapshot-replay-2"
        self.replay_mode: str | None = None
        self.replay_requests: list[dict[str, Any]] = []
        self.replay_failure: tuple[int, str] | None = None
        self.bundle_calls_at_replay: list[int] = []
        self.bundle_requests: list[dict[str, Any]] = []
        self.conclusion_requests: list[dict[str, Any]] = []
        self.last_ingestion: dict[str, Any] | None = None
        self.task_succeeded_served = False
        self.ingestion_failure: tuple[int, str] | None = None
        self.task_failure: str | None = None
        self.content = self._start(self._content_handler())
        self.agent = self._start(self._agent_handler())

    @staticmethod
    def _start(handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def close(self) -> None:
        self.content.shutdown(); self.agent.shutdown()

    @staticmethod
    def _body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        return json.loads(handler.rfile.read(int(handler.headers["Content-Length"])).decode())

    @staticmethod
    def _reply(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode(); handler.send_response(status); handler.send_header("Content-Type", "application/json"); handler.send_header("Content-Length", str(len(raw))); handler.end_headers(); handler.wfile.write(raw)

    def _content_handler(self):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                if self.path == "/v1/content/ingestions":
                    assert self.headers["Authorization"] == "Bearer fixture-token" and self.headers["X-Caller-Service"] == "stock_agent"
                    if outer.ingestion_failure:
                        outer._reply(self, outer.ingestion_failure[0], {"code": outer.ingestion_failure[1]}); return
                    body = outer._body(self)
                    if body.get("part") == 2:
                        outer._reply(self, 409, {"code": "IDEMPOTENCY_KEY_CONFLICT"}); return
                    outer.last_ingestion = body
                    outer._reply(self, 200, {"task_id": "task-1", "status": "PENDING"}); return
                if self.path == "/v1/content/knowledge-bundles":
                    outer.content_bundle_calls += 1
                    body = outer._body(self)
                    outer.bundle_requests.append(body)
                    if body.get("max_items") == 19:
                        outer._reply(self, 409, {"code": "IDEMPOTENCY_KEY_CONFLICT"}); return
                    outer._reply(self, 200, outer.active_bundle); return
                if self.path == "/api/v1/content-snapshots/snapshot-1/replay":
                    assert self.headers["Authorization"] == "Bearer fixture-token"
                    assert self.headers["X-Caller-Service"] == "stock_agent"
                    assert self.headers["X-Trace-Id"] == "trace-fixture"
                    body = outer._body(self)
                    outer.replay_requests.append(body)
                    if outer.replay_failure:
                        outer._reply(self, outer.replay_failure[0], {"detail": {"code": outer.replay_failure[1]}}); return
                    mode = body.get("mode")
                    if mode not in {"REPROCESS", "MIGRATION_REPLAY"}:
                        outer._reply(self, 422, {"detail": {"code": "INVALID_REPLAY_MODE"}}); return
                    if mode == "MIGRATION_REPLAY" and not body.get("pipeline_version"):
                        outer._reply(self, 422, {"detail": {"code": "INVALID_REPLAY_REQUEST"}}); return
                    outer.replay_mode = mode
                    outer.active_bundle = _bundle_for_snapshot(outer.bundle, outer.candidate_snapshot_id)
                    outer.active_conclusion = _conclusion(outer.active_bundle)
                    outer._reply(self, 200, {
                        "mode": mode, "source_snapshot_id": "snapshot-1",
                        "candidate_snapshot_id": outer.candidate_snapshot_id,
                        "comparison": {}, "differences": [],
                    }); return
                outer._reply(self, 404, {"code": "NOT_FOUND"})
            def do_GET(self):
                if self.path == "/v1/content/ingestions/task-1":
                    if outer.task_failure:
                        outer._reply(self, 200, {"task_id": "task-1", "status": "FAILED", "error": {"code": outer.task_failure}}); return
                    outer.task_succeeded_served = True
                    outer._reply(self, 200, {"task_id": "task-1", "status": "SUCCEEDED", "result": {"content_snapshot_id": "snapshot-1", "video_id": "video-1", "source": {"canonical_url": "https://example.test/video?token=hidden"}, "transcript_quality": {"status": "PASS"}, "knowledge_quality": {"status": "PASS"}}}); return
                if self.path == "/v1/content/knowledge-bundles/" + outer.active_bundle["bundle_id"]:
                    outer._reply(self, 200, outer.active_bundle); return
                if self.path == "/api/v1/content-snapshots/" + outer.candidate_snapshot_id:
                    outer._reply(self, 200, {"data": {
                        "content_snapshot_id": outer.candidate_snapshot_id,
                        "snapshot_kind": "MIGRATION" if outer.replay_mode == "MIGRATION_REPLAY" else "REPROCESS",
                        "parent_snapshot_id": "snapshot-1", "supersedes_snapshot_id": "snapshot-1",
                    }}); return
                if self.path == "/api/v1/content-snapshots/" + outer.candidate_snapshot_id + "/lineage":
                    outer._reply(self, 200, {"data": {
                        "lineage_complete": True,
                        "snapshot_lineage": {
                            "content_snapshot_id": outer.candidate_snapshot_id,
                            "snapshot_kind": "MIGRATION" if outer.replay_mode == "MIGRATION_REPLAY" else "REPROCESS",
                            "parent_snapshot_id": "snapshot-1", "supersedes_snapshot_id": "snapshot-1",
                            "parents": [{"content_snapshot_id": "snapshot-1"}],
                        },
                    }}); return
                outer._reply(self, 404, {"code": "NOT_FOUND"})
        return Handler

    def _agent_handler(self):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                if self.path == "/api/v2/knowledge-conclusions":
                    body = outer._body(self)
                    outer.conclusion_requests.append(body)
                    if body["query"] != outer.active_conclusion["query"]: outer._reply(self, 409, {"code": "IDEMPOTENCY_CONFLICT"}); return
                    outer._reply(self, 201, outer.active_conclusion); return
                if self.path.endswith("/replay"):
                    body = outer._body(self)
                    outer.bundle_calls_at_replay.append(outer.content_bundle_calls)
                    if body["mode"] == "VERIFY_HASH":
                        outer._reply(self, 200, {"audit_id": "audit-verify", "valid": True, "result_hash": canonical_hash(outer.active_conclusion)})
                        return
                    if body["mode"] == "RECOMPUTE_DETERMINISTIC":
                        outer._reply(self, 200, {"audit_id": "audit-deterministic", "result": outer.active_conclusion, "result_hash": canonical_hash(outer.active_conclusion)})
                        return
                    outer._reply(self, 422, {"code": "INVALID_REPLAY_MODE"}); return
                outer._reply(self, 404, {"code": "NOT_FOUND"})
            def do_GET(self):
                if self.path == "/health/version":
                    outer._reply(self, 200, {
                        "service": "stock_agent", "git_commit": "agent-runtime-sha",
                    }); return
                if self.path.endswith("/lineage"):
                    outer._reply(self, 200, {"bundle_id": outer.active_bundle["bundle_id"], "snapshot_id": outer.active_bundle["content_snapshot_id"], "result_hash": canonical_hash(outer.active_conclusion), "findings": [{"knowledge_id": "k-1", "evidence_id": "e-1"}]}); return
                if self.path.endswith("/conclusion-1"):
                    outer._reply(self, 200, outer.active_conclusion); return
                outer._reply(self, 404, {"code": "NOT_FOUND"})
        return Handler


def _args(tmp_path: Path, stack: _FixtureServer):
    token = tmp_path / "content.token"; token.write_text("fixture-token", encoding="utf-8")
    return parser().parse_args(["--content-url", f"http://127.0.0.1:{stack.content.server_port}", "--agent-url", f"http://127.0.0.1:{stack.agent.server_port}", "--content-token-file", str(token), "--source-type", "bilibili", "--source-ref", "BV1fixture", "--query", "核心逻辑、条件和风险是什么？", "--evidence-dir", str(tmp_path / "evidence"), "--trace-id", "trace-fixture", "--ingestion-idempotency-key", "ingestion-fixture", "--conclusion-idempotency-key", "conclusion-fixture"])


def test_fixture_flow_has_redacted_evidence_and_no_content_replay(tmp_path: Path) -> None:
    bundle = _bundle(); conclusion = _conclusion(bundle); stack = _FixtureServer(bundle, conclusion)
    try:
        report = run(_args(tmp_path, stack))
    finally:
        stack.close()
    evidence = Path(report["evidence_dir"])
    assert report["result"] == "PASS" and stack.content_bundle_calls == 3
    assert stack.bundle_calls_at_replay == [3, 3], "replay must use the frozen bundle and not call Content"
    assert stack.last_ingestion and stack.last_ingestion["options"] == {"offline_fixture": True}
    assert {"provenance.json", "ingestion-request.json", "task-final.json", "source-public-metadata.json", "transcript-quality.json", "knowledge-quality.json", "snapshot-manifest.json", "knowledge-bundle.json", "conclusion.json", "citation-validation.json", "secret-scan.json", "junit.xml"} <= {item.name for item in evidence.iterdir()}
    assert "?token" not in (evidence / "source-public-metadata.json").read_text(encoding="utf-8")
    assert scan(evidence)["result"] == "PASS"


def test_provenance_separates_runtime_refs_from_external_fact_verification(tmp_path: Path) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    args = _args(tmp_path, stack)
    args.content_sha, args.agent_sha = "producer-sha", "agent-runtime-sha"
    try:
        run(args)
    finally:
        stack.close()
    provenance = json.loads((tmp_path / "evidence" / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["exact_ref_gate"] == "PASS"
    assert provenance["reference_validation"] == {
        "method": "content_bundle.producer.git_commit + agent_health_version.git_commit",
        "requested": {"stock_content_sha": "producer-sha", "stock_agent_sha": "agent-runtime-sha"},
        "observed": {
            "stock_content_bundle_producer_git_commit": "producer-sha",
            "stock_agent_runtime_git_commit": "agent-runtime-sha",
        },
        "content_bundle_producer_ref": "MATCH", "agent_runtime_ref": "MATCH",
        "runtime_ref_match_gate": "PASS", "repository_commit_state": "NOT_VERIFIED",
        "main_merge_state": "NOT_VERIFIED",
    }
    assert provenance["evidence_validation"] == {
        "bundle_hash_integrity": "PASS", "citation_reference_integrity": "PASS",
        "conclusion_lineage_hash_integrity": "PASS",
        "external_fact_verification": "NOT_PERFORMED",
    }
    assert provenance["citation_precision"] == 1.0
    assert "hard_fact_grounding" not in provenance


def test_provenance_marks_unmatched_runtime_refs_without_claiming_merge(tmp_path: Path) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    try:
        run(_args(tmp_path, stack))
    finally:
        stack.close()
    provenance = json.loads((tmp_path / "evidence" / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["exact_ref_gate"] == "FAIL"
    assert provenance["reference_validation"]["content_bundle_producer_ref"] == "MISMATCH"
    assert provenance["reference_validation"]["agent_runtime_ref"] == "MISMATCH"
    assert provenance["reference_validation"]["main_merge_state"] == "NOT_VERIFIED"


@pytest.mark.parametrize("mutation", ("bundle", "citation", "action"))
def test_offline_verifiers_fail_closed_for_bounded_faults(mutation: str) -> None:
    bundle, conclusion = _bundle(), _conclusion(_bundle())
    if mutation == "bundle": bundle["items"][0]["statement"] = "tampered"
    elif mutation == "citation": conclusion["findings"][0]["evidence_ids"] = ["unknown"]
    else: conclusion["summary"] = "买入"
    with pytest.raises((BundleValidationError, GroundingError, ValueError, jsonschema.ValidationError)):
        verify_bundle(bundle) if mutation == "bundle" else verify_conclusion(conclusion, _bundle())


@pytest.mark.parametrize(("ingestion_failure", "task_failure", "code"), [
    ((504, "CONTENT_DEPENDENCY_TIMEOUT"), None, "CONTENT_DEPENDENCY_TIMEOUT"),
    (None, "WORKER_CRASHED_AFTER_ASR", "WORKER_CRASHED_AFTER_ASR"),
])
def test_fake_content_failures_have_stable_redacted_stage_codes(tmp_path: Path, ingestion_failure, task_failure, code: str) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    stack.ingestion_failure, stack.task_failure = ingestion_failure, task_failure
    try:
        with pytest.raises(E2EError, match=code):
            run(_args(tmp_path, stack))
    finally:
        stack.close()
    failure = json.loads((tmp_path / "evidence" / "failure.json").read_text(encoding="utf-8"))
    assert failure == {"code": code, "stage": "content-ingestion" if ingestion_failure else "content-task"}


def test_evidence_guards_and_secret_scanner_never_echo_matches(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"; occupied.mkdir(); (occupied / "prior.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="NOT_EMPTY"):
        prepare_evidence_dir(occupied)
    (occupied / "leak.json").write_text('{"authorization":"Bearer never-print-this"}', encoding="utf-8")
    report = scan(occupied)
    assert report["result"] == "FAIL" and "never-print-this" not in json.dumps(report)


def test_exact_epic_pair_keeps_candidate_compatibility_matrix_until_release_gates() -> None:
    matrix = yaml.safe_load((Path(__file__).parents[2] / "deploy/e2e/content-agent/compatibility-matrix.yaml").read_text(encoding="utf-8"))
    entry = matrix["entries"][0]
    assert entry["stock_content_sha"] == "ddd677fdb6931f42709a42c3246871bed13d0eef"
    assert entry["stock_agent_sha"] == "93d4701be24da95b17528872a4f49fdcaebc638e"
    assert entry["status"] == "candidate" and entry["exact_ref_gate"] == "PASS"
    assert "evidence_id" not in entry and "verified_at" not in entry


def test_replay_driver_rejects_shape_without_mode_specific_envelope() -> None:
    from scripts.e2e.run_content_agent_flow import _verify_replay_envelope

    with pytest.raises(E2EError, match="REPLAY_ENVELOPE_INVALID"):
        _verify_replay_envelope("VERIFY_HASH", {"conclusion_id": "conclusion-1"}, conclusion_id="conclusion-1")


def test_real_profile_omits_fixture_options_and_requires_persisted_readback(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    source_ref = tmp_path / "xiaoe-ref"; source_ref.write_text("p_demo/v_demo", encoding="utf-8")
    content_db, agent_db = "postgresql+psycopg://readonly:readonly@127.0.0.1:15432/content", "postgresql+psycopg://readonly:readonly@127.0.0.1:15433/agent"
    monkeypatch.setattr(
        "scripts.e2e.run_content_agent_flow._database_readback",
        lambda **_kwargs: {"content": {"task": 1}, "agent": {"run": 1}},
    )
    args = parser().parse_args([
        "--content-url", f"http://127.0.0.1:{stack.content.server_port}",
        "--agent-url", f"http://127.0.0.1:{stack.agent.server_port}",
        "--content-token-file", str(tmp_path / "content.token"),
        "--source-type", "xiaoe", "--source-ref-file", str(source_ref),
        "--credential-ref", "xiaoe-storage-state", "--query", "核心逻辑、条件和风险是什么？",
        "--evidence-dir", str(tmp_path / "real-evidence"), "--run-profile", "real",
        "--content-database-url", content_db, "--agent-database-url", agent_db,
        "--trace-id", "trace-real", "--ingestion-idempotency-key", "ingestion-real",
        "--conclusion-idempotency-key", "conclusion-real",
    ])
    (tmp_path / "content.token").write_text("fixture-token", encoding="utf-8")
    try:
        report = run(args)
    finally:
        stack.close()
    assert report["run_profile"] == "real"
    assert stack.last_ingestion and "options" not in stack.last_ingestion
    assert stack.last_ingestion["credential_ref"] == {"credential_ref": "xiaoe-storage-state", "provider": "file-secret"}
    provenance = json.loads((tmp_path / "real-evidence" / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["run_kind"] == "real-api-e2e" and provenance["database_readback"] == "PASS"


def test_default_clocks_are_observed_only_after_content_snapshot_succeeds(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    observed = "2026-09-09T12:34:56Z"

    def _clock_after_success() -> str:
        assert stack.task_succeeded_served
        return observed

    monkeypatch.setattr("scripts.e2e.run_content_agent_flow._observed_as_of", _clock_after_success)
    try:
        run(_args(tmp_path, stack))
    finally:
        stack.close()
    assert stack.bundle_requests[0]["business_as_of"] == observed
    assert stack.bundle_requests[0]["knowledge_as_of"] == observed
    assert stack.bundle_requests[0]["availability_as_of"] == observed
    assert stack.conclusion_requests[0]["business_as_of"] == observed
    provenance = json.loads((tmp_path / "evidence" / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["frozen_clocks"] == {
        "business_as_of": observed, "knowledge_as_of": observed,
        "availability_as_of": observed, "source": "observed_after_content_success",
    }


def test_explicit_historical_as_of_is_preserved_for_pit_tests(tmp_path: Path, monkeypatch) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    monkeypatch.setattr("scripts.e2e.run_content_agent_flow._observed_as_of", lambda: pytest.fail("historical clock must not be replaced"))
    args = _args(tmp_path, stack)
    args.as_of = "2024-01-02T03:04:05+08:00"
    try:
        run(args)
    finally:
        stack.close()
    expected = "2024-01-01T19:04:05Z"
    assert stack.bundle_requests[0]["business_as_of"] == expected
    assert stack.conclusion_requests[0]["availability_as_of"] == expected
    provenance = json.loads((tmp_path / "evidence" / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["frozen_clocks"]["source"] == "operator_supplied"


def test_v2_runner_keeps_producer_scope_and_consumer_contract_selection_aligned(tmp_path: Path) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    args = _args(tmp_path, stack)
    args.content_bundle_contract = "content-knowledge-bundle.v2"
    try:
        run(args)
    finally:
        stack.close()
    producer_body = stack.bundle_requests[0]
    consumer_payload = stack.conclusion_requests[0]
    assert producer_body["contract_version"] == "content-knowledge-bundle.v2"
    assert producer_body["subject_scope"] == "ALL_SUBJECTS"
    assert consumer_payload["content_bundle_contract"] == producer_body["contract_version"]


def test_migration_replay_uses_candidate_snapshot_for_bundle_conclusion_and_lineage(tmp_path: Path) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    args = _args(tmp_path, stack)
    args.content_replay_mode = "MIGRATION_REPLAY"
    args.content_replay_pipeline_version = "pipeline.v4"
    try:
        report = run(args)
    finally:
        stack.close()
    evidence = Path(report["evidence_dir"])
    assert stack.replay_requests == [{"mode": "MIGRATION_REPLAY", "pipeline_version": "pipeline.v4"}]
    assert stack.bundle_requests[0]["content_snapshot_id"] == stack.candidate_snapshot_id
    assert stack.conclusion_requests[0]["content_snapshot_id"] == stack.candidate_snapshot_id
    replay = json.loads((evidence / "content-snapshot-replay.json").read_text(encoding="utf-8"))
    candidate = json.loads((evidence / "candidate-snapshot-manifest.json").read_text(encoding="utf-8"))
    provenance = json.loads((evidence / "provenance.json").read_text(encoding="utf-8"))
    assert replay["candidate_snapshot_id"] == stack.candidate_snapshot_id
    assert candidate["snapshot"]["snapshot_kind"] == "MIGRATION"
    assert candidate["lineage"]["snapshot_lineage"]["parent_snapshot_id"] == "snapshot-1"
    assert provenance["source_snapshot_id"] == "snapshot-1"
    assert provenance["content_replay"] == {
        "mode": "MIGRATION_REPLAY", "pipeline_version": "pipeline.v4",
        "candidate_snapshot_id": stack.candidate_snapshot_id,
    }


def test_reprocess_replay_uses_candidate_snapshot_without_a_pipeline_override(tmp_path: Path) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    args = _args(tmp_path, stack)
    args.content_replay_mode = "REPROCESS"
    try:
        run(args)
    finally:
        stack.close()
    candidate = json.loads((tmp_path / "evidence" / "candidate-snapshot-manifest.json").read_text(encoding="utf-8"))
    assert stack.replay_requests == [{"mode": "REPROCESS"}]
    assert stack.bundle_requests[0]["content_snapshot_id"] == stack.candidate_snapshot_id
    assert candidate["snapshot"]["snapshot_kind"] == "REPROCESS"


def test_replay_rejects_missing_migration_version_before_http(tmp_path: Path) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    args = _args(tmp_path, stack)
    args.content_replay_mode = "MIGRATION_REPLAY"
    try:
        with pytest.raises(ValueError, match="MIGRATION_REPLAY_PIPELINE_VERSION_REQUIRED"):
            run(args)
    finally:
        stack.close()
    assert stack.replay_requests == []


def test_replay_failure_has_stable_content_stage_and_code(tmp_path: Path) -> None:
    bundle = _bundle(); stack = _FixtureServer(bundle, _conclusion(bundle))
    stack.replay_failure = (409, "REPLAY_ARTIFACT_HASH_MISMATCH")
    args = _args(tmp_path, stack)
    args.content_replay_mode = "REPROCESS"
    try:
        with pytest.raises(E2EError, match="REPLAY_ARTIFACT_HASH_MISMATCH"):
            run(args)
    finally:
        stack.close()
    failure = json.loads((tmp_path / "evidence" / "failure.json").read_text(encoding="utf-8"))
    assert failure == {"code": "REPLAY_ARTIFACT_HASH_MISMATCH", "stage": "content-snapshot-replay"}

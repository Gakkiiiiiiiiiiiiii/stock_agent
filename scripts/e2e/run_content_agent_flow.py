"""Run the fixed, knowledge-only Content -> Agent HTTP flow.

The ``fixture`` profile supports hermetic route tests.  The ``real`` profile
uses the same public routes, but deliberately never sends fixture-only input,
requires read-only database readback URLs, and records that the Agent used its
configured model or deterministic fallback.  It is deliberately not a generic
HTTP runner: it knows only the paired public route shapes and writes only
redacted, local evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ports.content_knowledge import (
    CONTENT_KNOWLEDGE_CONTRACT,
    CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM,
    CONTENT_KNOWLEDGE_V2_CONTRACT,
)
from scripts.e2e.common import (
    E2EError,
    atomic_json,
    fixed_base_url,
    prepare_evidence_dir,
    read_secret_file,
    redacted,
)
from scripts.e2e.scan_secrets import scan
from scripts.e2e.verify_knowledge_bundle import verify as verify_bundle
from scripts.e2e.verify_knowledge_conclusion import verify as verify_conclusion

_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "CANCELED"}


def _json_request(base: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None,
                  headers: dict[str, str] | None = None, timeout: float) -> tuple[int, dict[str, Any]]:
    # All callers below use literal route fragments.  This helper intentionally
    # has no user-provided method/path mechanism.
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() if payload is not None else None
    request = Request(base + path, data=body, method=method, headers={"Accept": "application/json", **(headers or {})})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw, status = response.read(), response.status
    except HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (URLError, TimeoutError) as exc:
        raise E2EError("HTTP_DEPENDENCY_UNAVAILABLE", "http") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise E2EError("HTTP_RESPONSE_INVALID", "http") from exc
    if not isinstance(parsed, dict):
        raise E2EError("HTTP_RESPONSE_INVALID", "http")
    return status, parsed


def _require(status: int, payload: dict[str, Any], *, stage: str, accepted: set[int]) -> dict[str, Any]:
    if status in accepted:
        return payload
    detail = payload.get("detail", payload)
    code = detail.get("code") if isinstance(detail, dict) else None
    raise E2EError(str(code or "HTTP_STATUS_" + str(status)), stage)


def _task_error(task: dict[str, Any]) -> str:
    error = task.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    if isinstance(task.get("error_code"), str):
        return task["error_code"]
    return "CONTENT_TASK_FAILED"


def _poll_task(content_url: str, task_id: str, headers: dict[str, str], *, timeout: float, interval: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    delay = max(0.05, interval)
    while time.monotonic() < deadline:
        status, task = _json_request(content_url, "/v1/content/ingestions/" + task_id, headers=headers, timeout=min(10.0, timeout))
        _require(status, task, stage="content-task", accepted={200})
        task_status = str(task.get("status", "")).upper()
        if task_status == "SUCCEEDED":
            return task
        if task_status in _TERMINAL:
            raise E2EError(_task_error(task), "content-task")
        time.sleep(delay)
        delay = min(delay * 1.5, 2.0)
    raise E2EError("CONTENT_TASK_TIMEOUT", "content-task")


def _snapshot_id(task: dict[str, Any]) -> str:
    result = task.get("result")
    value = result.get("content_snapshot_id") if isinstance(result, dict) else task.get("content_snapshot_id")
    if not isinstance(value, str) or not value.strip():
        raise E2EError("CONTENT_SNAPSHOT_MISSING", "content-task")
    return value


def _junit(directory: Path, *, name: str, error: E2EError | None) -> None:
    suite = ET.Element("testsuite", name="content-agent-e2e", tests="1", failures="0" if error is None else "1")
    case = ET.SubElement(suite, "testcase", name=name)
    if error is not None:
        ET.SubElement(case, "failure", type=error.code, message=error.stage)
    target = directory / "junit.xml"
    temporary = directory / ".tmp-junit.xml"
    ET.ElementTree(suite).write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(target)


def _as_of(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("AS_OF_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("AS_OF_INVALID")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _observed_as_of() -> str:
    """Freeze a real UTC clock after the Content snapshot is available."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _freeze_clocks(explicit_as_of: str | None) -> tuple[str, str]:
    """Return a single immutable clock and its audit-safe source label.

    A caller-provided ``--as-of`` is deliberately retained for PIT/replay
    negative tests.  The normal live path must not predate an ingestion that
    has not yet created its content snapshot, so it obtains its clock only
    after that snapshot exists.
    """
    if explicit_as_of is not None:
        return _as_of(explicit_as_of), "operator_supplied"
    return _observed_as_of(), "observed_after_content_success"


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _verify_replay_envelope(mode: str, replay: dict[str, Any], *, conclusion_id: str) -> None:
    """Validate the API's mode-specific replay envelope without refetching Content."""
    audit_id, result_hash = replay.get("audit_id"), replay.get("result_hash")
    if not isinstance(audit_id, str) or not audit_id or not isinstance(result_hash, str) or not result_hash:
        raise E2EError("REPLAY_ENVELOPE_INVALID", "agent-replay")
    if mode == "VERIFY_HASH":
        if replay.get("valid") is not True or "result" in replay:
            raise E2EError("REPLAY_VERIFY_INVALID", "agent-replay")
        return
    result = replay.get("result")
    if not isinstance(result, dict) or result.get("conclusion_id") != conclusion_id:
        raise E2EError("REPLAY_DETERMINISTIC_INVALID", "agent-replay")
    if _canonical_hash(result) != result_hash:
        raise E2EError("REPLAY_RESULT_HASH_INVALID", "agent-replay")


def _count(connection: Any, statement: str, **values: str) -> int:
    """Return one aggregate only; never serialize database rows or a DSN."""
    result = connection.execute(text(statement), values).scalar_one()
    return int(result or 0)


def _database_readback(
    *,
    content_database_url: str,
    agent_database_url: str,
    task_id: str,
    video_id: str,
    snapshot_id: str,
    bundle_id: str,
    conclusion_id: str,
) -> dict[str, dict[str, int]]:
    """Prove persisted effects through aggregate, read-only SQL queries.

    This remains an integration adapter, not a cross-repository ORM import:
    the Content table names are the published deployment schema identifiers,
    and only aggregate counts leave either database.
    """
    try:
        content_engine = create_engine(content_database_url, future=True)
        agent_engine = create_engine(agent_database_url, future=True)
        with content_engine.connect() as connection:
            content = {
                "task": _count(connection, "SELECT count(*) FROM content_ingest_task WHERE task_id=:task_id", task_id=task_id),
                "task_effect": _count(connection, "SELECT count(*) FROM content_task_effect WHERE task_id=:task_id", task_id=task_id),
                "snapshot": _count(connection, "SELECT count(*) FROM content_snapshot WHERE content_snapshot_id=:snapshot_id", snapshot_id=snapshot_id),
                "bundle": _count(connection, "SELECT count(*) FROM content_knowledge_bundle WHERE bundle_id=:bundle_id", bundle_id=bundle_id),
                "knowledge": _count(connection, "SELECT count(*) FROM knowledge_unit WHERE video_id=:video_id", video_id=video_id),
                "frame": _count(connection, "SELECT count(*) FROM video_frame WHERE video_id=:video_id", video_id=video_id),
                "ocr": _count(connection, "SELECT count(*) FROM ocr_evidence WHERE frame_id IN (SELECT frame_id FROM video_frame WHERE video_id=:video_id)", video_id=video_id),
                "vision": _count(connection, "SELECT count(*) FROM vision_evidence WHERE frame_id IN (SELECT frame_id FROM video_frame WHERE video_id=:video_id)", video_id=video_id),
            }
        with agent_engine.connect() as connection:
            agent = {
                "run": _count(connection, "SELECT count(*) FROM knowledge_conclusion_run WHERE conclusion_id=:conclusion_id", conclusion_id=conclusion_id),
                "citation": _count(connection, "SELECT count(*) FROM knowledge_conclusion_citation WHERE conclusion_id=:conclusion_id", conclusion_id=conclusion_id),
                "replay_audit": _count(connection, "SELECT count(*) FROM knowledge_conclusion_replay_audit WHERE conclusion_id=:conclusion_id", conclusion_id=conclusion_id),
                "lineage_audit": _count(connection, "SELECT count(*) FROM knowledge_conclusion_lineage_audit WHERE conclusion_id=:conclusion_id", conclusion_id=conclusion_id),
            }
    except SQLAlchemyError as exc:
        raise E2EError("DATABASE_READBACK_UNAVAILABLE", "database-readback") from exc
    if any(value <= 0 for group in (content, agent) for value in group.values()):
        raise E2EError("DATABASE_READBACK_INCOMPLETE", "database-readback")
    return {"content": content, "agent": agent}


def run(args: argparse.Namespace) -> dict[str, Any]:
    content_url, agent_url = fixed_base_url(args.content_url, label="CONTENT"), fixed_base_url(args.agent_url, label="AGENT")
    evidence = prepare_evidence_dir(args.evidence_dir)
    content_token = read_secret_file(args.content_token_file, code="CONTENT_TOKEN_FILE_INVALID")
    agent_token = read_secret_file(args.agent_token_file, code="AGENT_TOKEN_FILE_INVALID") if args.agent_token_file else None
    if args.source_type == "xiaoe":
        if not args.source_ref_file or args.source_ref:
            raise ValueError("XIAOE_SOURCE_REF_FILE_REQUIRED")
        source_ref = read_secret_file(args.source_ref_file, code="SOURCE_REF_FILE_INVALID")
    elif not args.source_ref or args.source_ref_file:
        raise ValueError("PUBLIC_SOURCE_REF_REQUIRED")
    else:
        source_ref = args.source_ref
    if args.run_profile == "real" and (not args.content_database_url or not args.agent_database_url):
        raise ValueError("REAL_DATABASE_READBACK_URLS_REQUIRED")
    if args.run_profile == "real" and args.source_type == "xiaoe" and not args.credential_ref:
        raise ValueError("XIAOE_CREDENTIAL_REF_REQUIRED")
    trace_id, ingestion_key, conclusion_key = args.trace_id or str(uuid.uuid4()), args.ingestion_idempotency_key or str(uuid.uuid4()), args.conclusion_idempotency_key or str(uuid.uuid4())
    content_headers = {"Authorization": "Bearer " + content_token, "X-Caller-Service": "stock_agent", "X-Trace-Id": trace_id, "Idempotency-Key": ingestion_key}
    agent_headers = {"X-Trace-Id": trace_id, "Idempotency-Key": conclusion_key}
    if agent_token:
        agent_headers["Authorization"] = "Bearer " + agent_token
    ingestion = {"source_type": args.source_type, "source_ref": source_ref, "part": 1, "transcript_policy": "subtitle_first"}
    if args.run_profile == "fixture":
        ingestion["options"] = {"offline_fixture": True}
    elif args.source_type == "xiaoe":
        # The reference is an operator-configured name.  The storage-state
        # bytes remain mounted only in the Content video worker.
        ingestion["credential_ref"] = {"credential_ref": args.credential_ref, "provider": "file-secret"}
    safe_ingestion = dict(ingestion)
    if args.source_type == "xiaoe":
        safe_ingestion["source_ref"] = "<secret-source-ref-file>"
    atomic_json(evidence, "ingestion-request.json", redacted(safe_ingestion))
    try:
        first_status, first = _json_request(content_url, "/v1/content/ingestions", method="POST", payload=ingestion, headers=content_headers, timeout=args.request_timeout)
        first = _require(first_status, first, stage="content-ingestion", accepted={200, 201})
        task_id = first.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise E2EError("CONTENT_TASK_ID_MISSING", "content-ingestion")
        duplicate_status, duplicate = _json_request(content_url, "/v1/content/ingestions", method="POST", payload=ingestion, headers=content_headers, timeout=args.request_timeout)
        duplicate = _require(duplicate_status, duplicate, stage="content-ingestion-idempotency", accepted={200, 201})
        if duplicate.get("task_id") != task_id:
            raise E2EError("CONTENT_IDEMPOTENCY_INVALID", "content-ingestion-idempotency")
        conflicting_ingestion = {**ingestion, "part": 2}
        conflict_status, _conflict = _json_request(content_url, "/v1/content/ingestions", method="POST", payload=conflicting_ingestion, headers=content_headers, timeout=args.request_timeout)
        if conflict_status != 409:
            raise E2EError("CONTENT_IDEMPOTENCY_CONFLICT_NOT_ENFORCED", "content-ingestion-idempotency-conflict")
        task = _poll_task(content_url, task_id, content_headers, timeout=args.poll_timeout, interval=args.poll_interval)
        atomic_json(evidence, "task-final.json", redacted(task))
        snapshot_id = _snapshot_id(task)
        clocks, clock_source = _freeze_clocks(args.as_of)
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        atomic_json(evidence, "source-public-metadata.json", redacted(result.get("source", result.get("source_public_metadata", {}))))
        atomic_json(evidence, "transcript-quality.json", redacted(result.get("transcript_quality", {})))
        atomic_json(evidence, "knowledge-quality.json", redacted(result.get("knowledge_quality", {})))
        atomic_json(evidence, "snapshot-manifest.json", redacted({"content_snapshot_id": snapshot_id, "task_id": task_id}))
        bundle_request = {"content_snapshot_id": snapshot_id, "query": args.query, "symbol": args.symbol,
                          "business_as_of": clocks, "knowledge_as_of": clocks, "availability_as_of": clocks,
                          "minimum_support_status": "SOURCE_SUPPORTED", "max_items": 20,
                          "policy": "PUBLIC_STRICT", "policy_version": "content-bundle-policy.v1"}
        if args.content_bundle_contract == CONTENT_KNOWLEDGE_V2_CONTRACT:
            # Content keeps v1 wire compatibility by omitting this field for
            # legacy runs.  The same choice is sent to the Agent so its own
            # immutable fetch cannot silently downgrade the direct v2 check.
            bundle_request["contract_version"] = CONTENT_KNOWLEDGE_V2_CONTRACT
            bundle_request["subject_scope"] = "ALL_SUBJECTS" if args.symbol.strip().upper() == "UNSPECIFIED" else "SUBJECT_ONLY"
        bundle_headers = {**content_headers, "Idempotency-Key": "bundle:" + conclusion_key}
        bundle_status, bundle = _json_request(content_url, "/v1/content/knowledge-bundles", method="POST", payload=bundle_request, headers=bundle_headers, timeout=args.request_timeout)
        bundle = _require(bundle_status, bundle, stage="content-bundle", accepted={200, 201})
        repeat_status, repeated_bundle = _json_request(content_url, "/v1/content/knowledge-bundles", method="POST", payload=bundle_request, headers=bundle_headers, timeout=args.request_timeout)
        repeated_bundle = _require(repeat_status, repeated_bundle, stage="content-bundle-idempotency", accepted={200, 201})
        if repeated_bundle.get("bundle_id") != bundle.get("bundle_id") or repeated_bundle.get("bundle_hash") != bundle.get("bundle_hash"):
            raise E2EError("BUNDLE_IDEMPOTENCY_INVALID", "content-bundle-idempotency")
        conflicting_bundle_request = {**bundle_request, "max_items": 19}
        conflict_status, _conflict = _json_request(content_url, "/v1/content/knowledge-bundles", method="POST", payload=conflicting_bundle_request, headers=bundle_headers, timeout=args.request_timeout)
        if conflict_status != 409:
            raise E2EError("BUNDLE_IDEMPOTENCY_CONFLICT_NOT_ENFORCED", "content-bundle-idempotency-conflict")
        bundle_id = bundle.get("bundle_id")
        if not isinstance(bundle_id, str) or not bundle_id:
            raise E2EError("BUNDLE_ID_MISSING", "content-bundle")
        bundle_read_status, stored_bundle = _json_request(content_url, "/v1/content/knowledge-bundles/" + bundle_id, headers=content_headers, timeout=args.request_timeout)
        stored_bundle = _require(bundle_read_status, stored_bundle, stage="content-bundle-readback", accepted={200})
        if stored_bundle.get("bundle_id") != bundle_id or stored_bundle.get("bundle_hash") != bundle.get("bundle_hash"):
            raise E2EError("BUNDLE_READBACK_INVALID", "content-bundle-readback")
        atomic_json(evidence, "knowledge-bundle.json", redacted(bundle))
        bundle_report = verify_bundle(bundle)
        atomic_json(evidence, "bundle-validation.json", bundle_report)
        conclusion_request = {"content_snapshot_id": snapshot_id, "query": args.query, "symbol": args.symbol,
                              "business_as_of": clocks, "knowledge_as_of": clocks, "availability_as_of": clocks}
        if args.content_bundle_contract == CONTENT_KNOWLEDGE_V2_CONTRACT:
            conclusion_request["content_bundle_contract"] = CONTENT_KNOWLEDGE_V2_CONTRACT
        conclusion_status, conclusion = _json_request(agent_url, "/api/v2/knowledge-conclusions", method="POST", payload=conclusion_request, headers=agent_headers, timeout=args.request_timeout)
        conclusion = _require(conclusion_status, conclusion, stage="agent-conclusion", accepted={200, 201})
        conclusion_id = conclusion.get("conclusion_id")
        if not isinstance(conclusion_id, str) or not conclusion_id:
            raise E2EError("CONCLUSION_ID_MISSING", "agent-conclusion")
        duplicate_status, duplicate = _json_request(agent_url, "/api/v2/knowledge-conclusions", method="POST", payload=conclusion_request, headers=agent_headers, timeout=args.request_timeout)
        duplicate = _require(duplicate_status, duplicate, stage="agent-idempotency", accepted={200, 201})
        if duplicate.get("conclusion_id") != conclusion_id:
            raise E2EError("CONCLUSION_IDEMPOTENCY_INVALID", "agent-idempotency")
        conflict_request = {**conclusion_request, "query": conclusion_request["query"] + "（不同请求）"}
        conflict_status, _conflict = _json_request(agent_url, "/api/v2/knowledge-conclusions", method="POST", payload=conflict_request, headers=agent_headers, timeout=args.request_timeout)
        if conflict_status != 409:
            raise E2EError("CONCLUSION_IDEMPOTENCY_CONFLICT_NOT_ENFORCED", "agent-idempotency-conflict")
        get_status, stored = _json_request(agent_url, "/api/v2/knowledge-conclusions/" + conclusion_id, headers=agent_headers, timeout=args.request_timeout)
        stored = _require(get_status, stored, stage="agent-status", accepted={200})
        if stored.get("conclusion_id") != conclusion_id:
            raise E2EError("CONCLUSION_STATUS_INVALID", "agent-status")
        lineage_status, lineage = _json_request(agent_url, "/api/v2/knowledge-conclusions/" + conclusion_id + "/lineage", headers=agent_headers, timeout=args.request_timeout)
        lineage = _require(lineage_status, lineage, stage="agent-lineage", accepted={200})
        atomic_json(evidence, "conclusion.json", redacted(conclusion))
        conclusion_report = verify_conclusion(conclusion, bundle, lineage)
        atomic_json(evidence, "citation-validation.json", conclusion_report)
        for mode in ("VERIFY_HASH", "RECOMPUTE_DETERMINISTIC"):
            replay_status, replay = _json_request(agent_url, "/api/v2/knowledge-conclusions/" + conclusion_id + "/replay", method="POST", payload={"mode": mode}, headers=agent_headers, timeout=args.request_timeout)
            replay = _require(replay_status, replay, stage="agent-replay-" + mode.lower(), accepted={200})
            _verify_replay_envelope(mode, replay, conclusion_id=conclusion_id)
        database_readback = None
        if args.run_profile == "real":
            video_id = str(result.get("video_id") or "")
            if not video_id:
                raise E2EError("CONTENT_VIDEO_ID_MISSING", "content-task")
            database_readback = _database_readback(
                content_database_url=args.content_database_url,
                agent_database_url=args.agent_database_url,
                task_id=task_id,
                video_id=video_id,
                snapshot_id=snapshot_id,
                bundle_id=bundle_id,
                conclusion_id=conclusion_id,
            )
            atomic_json(evidence, "database-readback.json", database_readback)
        model = conclusion.get("model") if isinstance(conclusion.get("model"), dict) else {}
        provenance = {"run_kind": args.run_profile + "-api-e2e", "trace_id": trace_id, "content_snapshot_id": snapshot_id,
                      "bundle_id": bundle["bundle_id"], "bundle_hash": bundle["bundle_hash"], "conclusion_id": conclusion_id,
                      "content_bundle_contract": args.content_bundle_contract,
                      "content_contract_checksum": bundle.get("producer", {}).get("contract_checksum", CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM), "stock_content_sha": args.content_sha,
                      "stock_agent_sha": args.agent_sha, "exact_ref_gate": "PENDING_UNCOMMITTED", "execution_eligible": False,
                      "citation_precision": 1.0, "hard_fact_grounding": 1.0, "timeout_seconds": args.poll_timeout,
                      "frozen_clocks": {"business_as_of": clocks, "knowledge_as_of": clocks,
                                        "availability_as_of": clocks, "source": clock_source},
                      "agent_model_mode": model.get("mode", "UNKNOWN"),
                      "agent_model_provider": model.get("provider", "UNKNOWN"),
                      "deterministic_fallback": model.get("mode") == "FALLBACK",
                      "database_readback": "PASS" if database_readback is not None else "NOT_REQUESTED"}
        atomic_json(evidence, "provenance.json", provenance)
        _junit(evidence, name="content_agent_" + args.run_profile + "_flow", error=None)
        secret_report = scan(evidence)
        atomic_json(evidence, "secret-scan.json", secret_report)
        if secret_report["result"] != "PASS":
            raise E2EError("SECRET_SCAN_FAILED", "secret-scan")
        return {"result": "PASS", "evidence_dir": str(evidence), "conclusion_id": conclusion_id,
                "bundle_id": bundle["bundle_id"], "run_profile": args.run_profile,
                "database_readback": database_readback}
    except E2EError as exc:
        atomic_json(evidence, "failure.json", {"stage": exc.stage, "code": exc.code})
        _junit(evidence, name="content_agent_" + args.run_profile + "_flow", error=exc)
        raise


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--content-url", required=True)
    value.add_argument("--agent-url", required=True)
    value.add_argument("--content-token-file", required=True)
    value.add_argument("--agent-token-file")
    value.add_argument("--source-type", choices=("bilibili", "xiaoe"), required=True)
    value.add_argument("--source-ref")
    value.add_argument("--source-ref-file")
    value.add_argument("--credential-ref")
    value.add_argument("--query", required=True)
    value.add_argument("--symbol", default="UNSPECIFIED")
    value.add_argument("--content-bundle-contract", choices=(CONTENT_KNOWLEDGE_CONTRACT, CONTENT_KNOWLEDGE_V2_CONTRACT), default=CONTENT_KNOWLEDGE_CONTRACT)
    value.add_argument("--evidence-dir", required=True)
    value.add_argument("--run-profile", choices=("fixture", "real"), default="fixture")
    value.add_argument("--content-database-url")
    value.add_argument("--agent-database-url")
    value.add_argument("--as-of", help="Explicit historical/as-of UTC instant; omit to freeze after Content succeeds")
    value.add_argument("--trace-id")
    value.add_argument("--ingestion-idempotency-key")
    value.add_argument("--conclusion-idempotency-key")
    value.add_argument("--content-sha", default="bfc7f9be6b03f3189d2d316934e31a299e36047c")
    value.add_argument("--agent-sha", default="96f63d8a56567ce1b60788f0d59880472268b74c")
    value.add_argument("--request-timeout", type=float, default=10.0)
    value.add_argument("--poll-timeout", type=float, default=900.0)
    value.add_argument("--poll-interval", type=float, default=0.2)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        report = run(args)
    except (E2EError, ValueError) as exc:
        code = exc.code if isinstance(exc, E2EError) else str(exc)
        sys.stderr.write("E2E_FAILED " + code + "\n")
        return 1
    sys.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

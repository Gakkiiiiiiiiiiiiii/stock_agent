"""Verify the independently pinned stock_content knowledge-bundle contract.

This intentionally does not call the platform manifest verifier: unrelated
producer contracts must not decide whether this consumer boundary ran.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.application.knowledge_conclusion.bundle_validator import (
    BundleValidationError,
    ContentKnowledgeBundleValidator,
    canonical_json,
)
from app.ports.content_knowledge import CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM

CONTRACT = "content-knowledge-bundle.v1"
SCHEMA_RELATIVE = Path("contracts/content-knowledge-bundle.v1.json")
VENDORED_SCHEMA_RELATIVE = Path("contracts/fixtures/content-knowledge-bundle.v1.json")
C14N_RELATIVE = Path("contracts/fixtures/content-knowledge-bundle.c14n-v1.json")
SHA = re.compile(r"^[0-9a-f]{40}$")
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT")
SET_ARRAYS = frozenset({"reason_codes", "warnings", "evidence_refs", "evidence_ids"})


def _checksum(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest().upper()


def independent_canonical_json(value: Any, *, parent_key: str | None = None) -> bytes:
    """Small independent c14n-v1 implementation used only by the contract gate."""
    if value is None:
        result = "null"
    elif value is True:
        result = "true"
    elif value is False:
        result = "false"
    elif isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value)
        if TIMESTAMP.match(normalized):
            try:
                parsed = datetime.fromisoformat(normalized)
                if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                    normalized = parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z").replace(".000000Z", "Z")
            except ValueError:
                pass
        result = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(value, Decimal):
        result = _independent_decimal(value)
    elif isinstance(value, int) and not isinstance(value, bool):
        result = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        result = _independent_decimal(Decimal(str(value)))
    elif isinstance(value, Mapping):
        entries = {unicodedata.normalize("NFC", key): item for key, item in value.items() if isinstance(key, str)}
        if len(entries) != len(value):
            raise TypeError("non-string object key")
        result = "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + independent_canonical_json(item, parent_key=key).decode("utf-8")
            for key, item in sorted(entries.items())
        ) + "}"
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        values = list(value)
        if parent_key in SET_ARRAYS:
            values.sort(key=lambda item: independent_canonical_json(item).decode("utf-8"))
        elif parent_key == "items" and all(isinstance(item, Mapping) for item in values):
            values.sort(key=_item_key)
        elif parent_key == "evidence":
            values.sort(key=_evidence_key)
        result = "[" + ",".join(independent_canonical_json(item, parent_key=parent_key).decode("utf-8") for item in values) + "]"
    else:
        raise TypeError("unsupported canonical value")
    return result.encode("utf-8")


def _item_key(item: Mapping[str, Any]) -> tuple[str, ...]:
    subject = item.get("subject") if isinstance(item.get("subject"), Mapping) else {}
    temporal = item.get("temporal") if isinstance(item.get("temporal"), Mapping) else {}
    return (
        str(subject.get("type", "")), str(subject.get("key", "")), str(item.get("predicate", "")),
        str(temporal.get("target_start", "")), str(temporal.get("target_end", "")),
        str(item.get("occurrence_id", "")), str(item.get("knowledge_id", item.get("claim_id", ""))),
    )


def _evidence_key(item: Any) -> tuple[Any, ...] | str:
    if not isinstance(item, Mapping):
        return independent_canonical_json(item).decode("utf-8")
    return (str(item.get("evidence_id", "")), item.get("start_ms", -1), item.get("end_ms", -1))


def _independent_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non-finite number")
    if value == 0:
        return "0"
    try:
        result = format(value.normalize(), "f")
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid decimal") from exc
    return result.rstrip("0").rstrip(".") if "." in result else result


def _git_state(root: Path) -> tuple[str | None, bool | None]:
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip().lower()
        dirty = bool(subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"], check=True, capture_output=True, text=True
        ).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return sha if SHA.fullmatch(sha) else None, dirty


def _schema_nodes(value: Any, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], dict[str, Any]]:
    nodes: dict[tuple[str, ...], dict[str, Any]] = {}
    if not isinstance(value, dict):
        return nodes
    properties = value.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            if isinstance(name, str) and isinstance(child, dict):
                child_path = path + (name,)
                nodes[child_path] = child
                nodes.update(_schema_nodes(child, child_path))
    items = value.get("items")
    if isinstance(items, dict):
        nodes.update(_schema_nodes(items, path + ("[]",)))
    return nodes


def _required(schema: dict[str, Any]) -> set[str]:
    fields = schema.get("required", [])
    return set(fields) if isinstance(fields, list) and all(isinstance(item, str) for item in fields) else set()


def _contract_version(schema: dict[str, Any]) -> str | None:
    value = schema.get("properties", {}).get("contract", {}) if isinstance(schema.get("properties"), dict) else {}
    return value.get("const") if isinstance(value, dict) and isinstance(value.get("const"), str) else None


def compatibility_codes(baseline: dict[str, Any], candidate: dict[str, Any], *, c14n_changed: bool) -> set[str]:
    """Classify conservative v1 changes; an unrecognized addition is hash material."""
    codes: set[str] = set()
    base_nodes, candidate_nodes = _schema_nodes(baseline), _schema_nodes(candidate)
    all_objects = [((), baseline, candidate)]
    all_objects.extend((path, base_nodes[path], candidate_nodes.get(path, {})) for path in base_nodes)
    for path, old, new in all_objects:
        removed = _required(old) - _required(new)
        if removed:
            codes.add("REQUIRED_FIELD_REMOVED")
            names = " ".join(removed).casefold()
            if "evidence" in names or "ownership" in names:
                codes.add("EVIDENCE_OWNERSHIP_CHANGED")
            if any(token in names for token in ("snapshot", "as_of", "known_at", "available_at", "business_at")):
                codes.add("SNAPSHOT_PIT_AS_OF_CHANGED")
        old_type, new_type = old.get("type"), new.get("type")
        if old_type is not None and new_type is not None and old_type != new_type:
            codes.add("TYPE_CHANGED")
        old_enum, new_enum = old.get("enum"), new.get("enum")
        if isinstance(old_enum, list) and isinstance(new_enum, list) and set(new_enum) < set(old_enum):
            codes.add("ENUM_NARROWED")
        name = ".".join(path).casefold()
        if any(token in name for token in ("evidence", "ownership")) and old != new:
            codes.add("EVIDENCE_OWNERSHIP_CHANGED")
        if any(token in name for token in ("snapshot", "as_of", "known_at", "available_at", "business_at")) and old != new:
            codes.add("SNAPSHOT_PIT_AS_OF_CHANGED")
        if "evidence" in name and isinstance(old.get("minItems"), int) and old["minItems"] >= 1 and (
            not isinstance(new.get("minItems"), int) or new["minItems"] < 1
        ):
            codes.add("NO_EVIDENCE_ALLOWED")
    for path, node in candidate_nodes.items():
        if path in base_nodes:
            continue
        diagnostic_contract = node.get("x-diagnostic-contract")
        if (
            "default" in node
            and node.get("x-hash-material") is False
            and isinstance(diagnostic_contract, str)
            and re.fullmatch(r"[a-z0-9-]+\.v[1-9][0-9]*", diagnostic_contract)
        ):
            codes.add("COMPATIBLE_DIAGNOSTIC_METADATA_ADDED")
        else:
            codes.add("HASH_MATERIAL_ADDITION")
    if c14n_changed:
        codes.add("CANONICALIZATION_OR_HASH_MATERIAL_CHANGED")
    if codes & {
        "REQUIRED_FIELD_REMOVED", "TYPE_CHANGED", "ENUM_NARROWED", "EVIDENCE_OWNERSHIP_CHANGED",
        "SNAPSHOT_PIT_AS_OF_CHANGED", "NO_EVIDENCE_ALLOWED", "HASH_MATERIAL_ADDITION",
        "CANONICALIZATION_OR_HASH_MATERIAL_CHANGED",
    } and _contract_version(candidate) in {None, CONTRACT}:
        codes.add("V2_REQUIRED")
    return codes


def _valid_payload() -> dict[str, Any]:
    request = {
        "content_snapshot_id": "contract-fixture-snapshot", "query": "contract fixture", "symbol": "600519.SH",
        "business_as_of": "2026-09-06T00:00:00Z", "knowledge_as_of": "2026-09-06T00:00:00Z",
        "availability_as_of": "2026-09-06T00:00:00Z", "minimum_support_status": "SOURCE_SUPPORTED",
        "max_items": 1, "policy": "PUBLIC_STRICT", "policy_version": "content-bundle-policy.v1",
    }
    payload: dict[str, Any] = {
        "contract": CONTRACT, "schema_version": "1.0.0", "canonicalization_version": "content-bundle-c14n-v1",
        "request": request, "request_hash": "", "content_snapshot_id": request["content_snapshot_id"], "query": request["query"],
        "source": {"source_type": "bilibili", "source_identity_hash": "source-1", "source_version_id": "version-1", "canonical_url": "https://example.com/report", "source_content_hash": "content-1"}, "business_as_of": request["business_as_of"], "knowledge_as_of": request["knowledge_as_of"],
        "availability_as_of": request["availability_as_of"],
        "items": [{"knowledge_id": "contract-fixture-item", "claim_id": "claim-1", "occurrence_id": "occurrence-1",
                   "statement": "fixture", "subject": {"type": "EQUITY", "key": "600519.SH"}, "predicate": "fixture", "object": {"value": "fixture", "unit": None},
                   "temporal": {"target_start": "2026-09-06T00:00:00Z", "target_end": "2026-09-06T00:00:00Z", "precision": "INSTANT"},
                   "support_status": "SOURCE_SUPPORTED", "lifecycle_status": "ACTIVE", "grounding_status": "GROUNDED", "verification": {"status": "VERIFIED", "reason_codes": []},
                   "evidence": [{"evidence_id": "evidence-1", "ownership": "PRIMARY", "artifact_id": "artifact-1", "segment_id": "segment-1", "start_ms": 0, "end_ms": 1, "quote": "fixture", "quote_hash": "fixture-hash", "modality": "TRANSCRIPT"}]}],
        "quality": {"knowledge_count": 1, "grounded_ratio": 1, "numeric_grounded_ratio": 1, "warnings": []},
        "producer": {"service": "stock_content", "service_version": "1", "git_commit": "0" * 40,
                     "pipeline_version": "1", "contract_checksum": CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM},
    }
    payload["request_hash"] = "sha256:" + hashlib.sha256(independent_canonical_json(request)).hexdigest()
    digest = hashlib.sha256(independent_canonical_json(payload)).hexdigest()
    payload["bundle_id"], payload["bundle_hash"] = "ckb_" + digest, "sha256:" + digest
    return payload


def _verify_c14n(c14n: bytes, codes: set[str]) -> None:
    try:
        fixture = json.loads(c14n)
        vectors = fixture["vectors"]
        if fixture.get("canonicalization_version") != "content-bundle-c14n-v1" or not isinstance(vectors, list):
            raise ValueError
        for vector in vectors:
            if not isinstance(vector, dict) or not isinstance(vector.get("name"), str):
                raise TypeError
            independent_left, independent_right = independent_canonical_json(vector["left"]), independent_canonical_json(vector["right"])
            consumer_left, consumer_right = canonical_json(vector["left"]), canonical_json(vector["right"])
            if independent_left != consumer_left or independent_right != consumer_right:
                codes.add("INDEPENDENT_CANONICAL_HASH_MISMATCH")
            if (vector["name"] == "tamper") == (independent_left == independent_right):
                raise ValueError
        for rejected in (float("nan"), float("inf")):
            for implementation in (independent_canonical_json, canonical_json):
                try:
                    implementation({"rejected": rejected})
                except (BundleValidationError, ValueError):
                    continue
                codes.add("C14N_VECTOR_INVALID")
    except (KeyError, TypeError, ValueError, BundleValidationError, json.JSONDecodeError):
        codes.add("C14N_VECTOR_INVALID")


def _producer_manifest_ok(root: Path, checksum: str, codes: set[str]) -> None:
    try:
        manifest = yaml.safe_load((root / "contracts/platform-manifest.yaml").read_text(encoding="utf-8")) or {}
        entries = manifest.get("contracts", [])
        entry = next(item for item in entries if isinstance(item, dict) and item.get("id") == CONTRACT)
    except (OSError, StopIteration, yaml.YAMLError, TypeError):
        codes.add("PRODUCER_MANIFEST_ENTRY_MISSING")
        return
    if entry.get("producer") != "stock_content" or entry.get("owner") != "content-platform":
        codes.add("PRODUCER_MANIFEST_OWNERSHIP_INVALID")
    if entry.get("schema") != SCHEMA_RELATIVE.as_posix() or str(entry.get("checksum", "")).casefold() != checksum.casefold():
        codes.add("PRODUCER_MANIFEST_CHECKSUM_INVALID")
    try:
        schema = json.loads((root / SCHEMA_RELATIVE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        codes.add("PRODUCER_MANIFEST_VERSION_INVALID")
        return
    if _contract_version(schema) != CONTRACT:
        codes.add("PRODUCER_MANIFEST_VERSION_INVALID")


def verify(*, producer_root: Path, expected_producer_sha: str, expected_checksum: str, consumer_sha: str | None, strict_ref: bool) -> dict[str, Any]:
    codes: set[str] = set()
    producer_sha, producer_dirty = _git_state(producer_root)
    local_consumer_sha, consumer_dirty = _git_state(PROJECT_ROOT)
    report: dict[str, Any] = {
        "report_version": "content-knowledge-compatibility-report.v1",
        "producer": {"repo": "stock_content", "sha": producer_sha or "UNRESOLVED", "expected_sha": expected_producer_sha,
                     "contract": CONTRACT, "checksum": expected_checksum},
        "consumer": {"repo": "stock_agent", "sha": local_consumer_sha or "UNRESOLVED", "expected_sha": consumer_sha or "UNSPECIFIED"},
        "exact_ref_gate": "PASS", "reason_codes": [], "result": "PASS",
    }
    if strict_ref:
        if not SHA.fullmatch(expected_producer_sha.lower()):
            codes.add("EXPECTED_PRODUCER_SHA_INVALID")
        if consumer_sha is None or not SHA.fullmatch(consumer_sha.lower()):
            codes.add("EXPECTED_CONSUMER_SHA_INVALID")
        if producer_sha != expected_producer_sha.lower() or producer_dirty is not False:
            codes.add("PRODUCER_EXACT_REF_GATE_FAILED")
        if local_consumer_sha != (consumer_sha or "").lower() or consumer_dirty is not False:
            codes.add("CONSUMER_EXACT_REF_GATE_FAILED")
        if codes & {"EXPECTED_PRODUCER_SHA_INVALID", "EXPECTED_CONSUMER_SHA_INVALID", "PRODUCER_EXACT_REF_GATE_FAILED", "CONSUMER_EXACT_REF_GATE_FAILED"}:
            report["exact_ref_gate"] = "FAIL"
    else:
        report["exact_ref_gate"] = "PENDING_UNCOMMITTED"
    producer_schema_path, vendored_schema_path = producer_root / SCHEMA_RELATIVE, PROJECT_ROOT / VENDORED_SCHEMA_RELATIVE
    producer_c14n_path, vendored_c14n_path = producer_root / C14N_RELATIVE, PROJECT_ROOT / C14N_RELATIVE
    try:
        producer_schema, vendored_schema = producer_schema_path.read_bytes(), vendored_schema_path.read_bytes()
        candidate, baseline = json.loads(producer_schema), json.loads(vendored_schema)
    except (OSError, json.JSONDecodeError):
        codes.add("SCHEMA_UNREADABLE")
        candidate = baseline = {}
        producer_schema = b""
        vendored_schema = b""
    actual_checksum = _checksum(producer_schema)
    report["producer"]["actual_checksum"] = actual_checksum
    if actual_checksum.casefold() != expected_checksum.casefold():
        codes.add("PRODUCER_SCHEMA_CHECKSUM_MISMATCH")
    if producer_schema != vendored_schema:
        codes.add("CONSUMER_SCHEMA_LOCK_MISMATCH")
    try:
        producer_c14n, vendored_c14n = producer_c14n_path.read_bytes(), vendored_c14n_path.read_bytes()
        c14n_changed = producer_c14n != vendored_c14n
        if c14n_changed:
            codes.add("C14N_FIXTURE_MISMATCH")
        _verify_c14n(producer_c14n, codes)
    except OSError:
        c14n_changed = True
        codes.add("C14N_FIXTURE_MISSING")
    codes.update(compatibility_codes(baseline, candidate, c14n_changed=c14n_changed))
    _producer_manifest_ok(producer_root, actual_checksum, codes)
    try:
        payload = _valid_payload()
        material = {key: value for key, value in payload.items() if key not in {"bundle_id", "bundle_hash"}}
        if payload["bundle_hash"] != "sha256:" + hashlib.sha256(independent_canonical_json(material)).hexdigest():
            codes.add("INDEPENDENT_CANONICAL_HASH_MISMATCH")
        ContentKnowledgeBundleValidator().validate(payload)
    except (BundleValidationError, RuntimeError, OSError):
        codes.add("CONSUMER_VALIDATOR_REJECTED_GOLDEN")
    report["reason_codes"] = sorted(codes)
    if codes:
        report["result"] = "FAIL"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--producer-root", type=Path, required=True)
    parser.add_argument("--expected-producer-sha", required=True)
    parser.add_argument("--expected-checksum", default=CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM)
    parser.add_argument("--consumer-sha")
    parser.add_argument("--report", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--strict-ref", action="store_true")
    mode.add_argument("--working-tree", action="store_true")
    args = parser.parse_args()
    report = verify(producer_root=args.producer_root, expected_producer_sha=args.expected_producer_sha,
                    expected_checksum=args.expected_checksum, consumer_sha=args.consumer_sha, strict_ref=args.strict_ref)
    args.report.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

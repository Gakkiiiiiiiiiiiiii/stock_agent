"""Fail-closed validation and c14n-v1 hashing for content knowledge bundles."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

try:  # Keep pure replay/lineage imports available in minimal worker images.
    import jsonschema
except ModuleNotFoundError:  # pragma: no cover - ingress deployment locks this dependency
    jsonschema = None

from app.domain.knowledge_conclusion_run import FrozenBundle
from app.ports.content_knowledge import (
    CONTENT_KNOWLEDGE_C14N_VERSION,
    CONTENT_KNOWLEDGE_CONTRACT,
    CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM,
    CONTENT_KNOWLEDGE_SCHEMA_VERSION,
    KNOWLEDGE_POLICY_VERSION,
    MINIMUM_SUPPORT_STATUS,
    PUBLIC_STRICT,
    ContentKnowledgeBundle,
    KnowledgeBundleRequest,
)
from contracts.immutable import freeze

_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_PATH = _ROOT / "contracts" / "fixtures" / "content-knowledge-bundle.v1.json"
_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT")
_SECRET = re.compile(r"(?:^|_)(?:authorization|token|secret|api_key|password|cookie)(?:$|_)", re.IGNORECASE)
_UNSAFE_CONTENT_KEY = re.compile(r"(?:cookie|header|storage|signed|prompt|raw_response|pii)", re.IGNORECASE)
_SET_ARRAYS = frozenset({"reason_codes", "warnings", "evidence_refs", "evidence_ids"})


class BundleValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_json(value: Any, *, parent_key: str | None = None) -> bytes:
    """Independent content-bundle-c14n-v1 encoder (not Python's JSON default)."""
    return _canonical(value, parent_key).encode("utf-8")


def _canonical(value: Any, parent_key: str | None) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(_timestamp_or_text(value), ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BundleValidationError("CONTENT_SCHEMA_INVALID")
        return _decimal(Decimal(str(value)))
    if isinstance(value, Mapping):
        entries: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BundleValidationError("CONTENT_SCHEMA_INVALID")
            normalized = unicodedata.normalize("NFC", key)
            entries[normalized] = item
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + _canonical(item, key) for key, item in sorted(entries.items())) + "}"
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        values = list(value)
        if parent_key in _SET_ARRAYS:
            values.sort(key=lambda row: _canonical(row, None))
        elif parent_key == "items" and all(isinstance(row, Mapping) for row in values):
            values.sort(key=_item_key)
        elif parent_key == "evidence":
            values.sort(key=_evidence_key)
        return "[" + ",".join(_canonical(item, parent_key) for item in values) + "]"
    raise BundleValidationError("CONTENT_SCHEMA_INVALID")


def _decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise BundleValidationError("CONTENT_SCHEMA_INVALID")
    if value == 0:
        return "0"
    try:
        output = format(value.normalize(), "f")
    except (InvalidOperation, ValueError) as exc:
        raise BundleValidationError("CONTENT_SCHEMA_INVALID") from exc
    if "." in output:
        output = output.rstrip("0").rstrip(".")
    return output or "0"


def _timestamp_or_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if not _TIMESTAMP.match(normalized):
        return normalized
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return normalized
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return normalized
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z").replace(".000000Z", "Z")


def _item_key(item: Mapping[str, Any]) -> tuple[str, ...]:
    subject = item.get("subject") or {}
    temporal = item.get("temporal") or {}
    if not isinstance(subject, Mapping):
        subject = {}
    if not isinstance(temporal, Mapping):
        temporal = {}
    return (
        str(subject.get("type", "")), str(subject.get("key", "")), str(item.get("predicate", "")),
        str(temporal.get("target_start", "")), str(temporal.get("target_end", "")),
        str(item.get("occurrence_id", "")), str(item.get("knowledge_id", item.get("claim_id", ""))),
    )


def _evidence_key(item: Any) -> tuple[Any, ...] | str:
    if not isinstance(item, Mapping):
        return _canonical(item, None)
    return (str(item.get("evidence_id", "")), item.get("start_ms", -1), item.get("end_ms", -1))


class ContentKnowledgeBundleValidator:
    def __init__(self, schema_path: Path = _SCHEMA_PATH) -> None:
        if jsonschema is None:
            raise RuntimeError("CONTENT_SCHEMA_VALIDATOR_UNAVAILABLE")
        schema_bytes = schema_path.read_bytes()
        checksum = "sha256:" + hashlib.sha256(schema_bytes).hexdigest().upper()
        if checksum != CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM:
            raise RuntimeError("CONTENT_SCHEMA_LOCK_INVALID")
        self._schema = json.loads(schema_bytes)
        self._schema_validator = jsonschema.Draft202012Validator(self._schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)

    def request_payload(self, request: KnowledgeBundleRequest) -> dict[str, Any]:
        if not all(isinstance(value, str) and value.strip() for value in (request.content_snapshot_id, request.query, request.symbol)):
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        if request.content_snapshot_id.strip().casefold() in {"latest", "current", "default"}:
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        if request.minimum_support_status != MINIMUM_SUPPORT_STATUS or request.policy != PUBLIC_STRICT or request.policy_version != KNOWLEDGE_POLICY_VERSION:
            raise BundleValidationError("CONTENT_CONTRACT_MISMATCH")
        if not isinstance(request.max_items, int) or isinstance(request.max_items, bool) or not 1 <= request.max_items <= 100:
            raise BundleValidationError("CONTENT_SCHEMA_INVALID")
        clocks = (request.business_as_of, request.knowledge_as_of, request.availability_as_of)
        if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None for value in clocks):
            raise BundleValidationError("CONTENT_AS_OF_VIOLATION")
        return {
            "content_snapshot_id": request.content_snapshot_id.strip(), "query": request.query.strip(), "symbol": request.symbol.strip(),
            "business_as_of": _as_z(request.business_as_of), "knowledge_as_of": _as_z(request.knowledge_as_of),
            "availability_as_of": _as_z(request.availability_as_of), "minimum_support_status": MINIMUM_SUPPORT_STATUS,
            "max_items": request.max_items, "policy": PUBLIC_STRICT, "policy_version": KNOWLEDGE_POLICY_VERSION,
        }

    def request_hash(self, request: Mapping[str, Any]) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(request)).hexdigest()

    @staticmethod
    def verify_integrity(payload: Mapping[str, Any]) -> tuple[str, str]:
        """Verify the locked c14n-v1 identity material without re-fetching.

        This is shared by the ingress validator and frozen-lineage reader so
        both use exactly the producer's ``bundle_id``/``bundle_hash`` rule.
        Schema and policy validation remain ingress responsibilities.
        """
        data = dict(payload)
        if (
            data.get("contract") != CONTENT_KNOWLEDGE_CONTRACT
            or data.get("schema_version") != CONTENT_KNOWLEDGE_SCHEMA_VERSION
            or data.get("canonicalization_version") != CONTENT_KNOWLEDGE_C14N_VERSION
        ):
            raise BundleValidationError("CONTENT_CONTRACT_MISMATCH")
        producer = data.get("producer")
        if not isinstance(producer, Mapping) or producer.get("service") != "stock_content":
            raise BundleValidationError("CONTENT_CONTRACT_MISMATCH")
        if producer.get("contract_checksum") != CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM:
            raise BundleValidationError("CONTENT_CONTRACT_MISMATCH")
        material = {key: value for key, value in data.items() if key not in {"bundle_id", "bundle_hash"}}
        digest = hashlib.sha256(canonical_json(material)).hexdigest()
        expected_hash = "sha256:" + digest
        expected_id = "ckb_" + digest
        if data.get("bundle_hash") != expected_hash or data.get("bundle_id") != expected_id:
            raise BundleValidationError("CONTENT_BUNDLE_TAMPERED")
        return expected_id, expected_hash

    def validate(self, payload: Mapping[str, Any], *, expected: KnowledgeBundleRequest | None = None) -> ContentKnowledgeBundle:
        data = dict(payload)
        # Header/status are checked by the adapter.  These checks intentionally follow the locked order.
        self.verify_integrity(data)
        try:
            self._schema_validator.validate(data)
        except jsonschema.ValidationError as exc:
            raise BundleValidationError("CONTENT_SCHEMA_INVALID") from exc
        producer = data.get("producer")
        assert isinstance(producer, Mapping)  # verified above
        request = data.get("request")
        if not isinstance(request, Mapping) or set(request) != {"content_snapshot_id", "query", "symbol", "business_as_of", "knowledge_as_of", "availability_as_of", "minimum_support_status", "max_items", "policy", "policy_version"}:
            raise BundleValidationError("CONTENT_SCHEMA_INVALID")
        self._validate_request_bindings(request)
        if data.get("request_hash") != self.request_hash(request):
            raise BundleValidationError("CONTENT_BUNDLE_TAMPERED")
        if expected is not None and data.get("request_hash") != self.request_hash(self.request_payload(expected)):
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        if data.get("content_snapshot_id") != request["content_snapshot_id"] or data.get("query") != request["query"]:
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        self._validate_pit(data, request)
        self._validate_items(data)
        frozen = freeze(data)
        return ContentKnowledgeBundle(
            bundle_id=str(data["bundle_id"]), bundle_hash=str(data["bundle_hash"]), request_hash=str(data["request_hash"]),
            content_snapshot_id=str(data["content_snapshot_id"]), producer_sha=str(producer["git_commit"]),
            contract_checksum=CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM, payload=MappingProxyType(frozen),
        )

    def freeze_for_conclusion(self, bundle: ContentKnowledgeBundle) -> FrozenBundle:
        """The only SA-02 handoff to SA-05's existing persistence boundary."""
        return FrozenBundle(bundle.bundle_id, bundle.bundle_hash, freeze(dict(bundle.payload)), bundle.producer_sha, bundle.contract_checksum, bundle.content_snapshot_id)

    def _validate_pit(self, data: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        clocks = {name: _parse_time(str(request[name])) for name in ("business_as_of", "knowledge_as_of", "availability_as_of")}
        for name in ("business_as_of", "knowledge_as_of", "availability_as_of"):
            if _parse_time(str(data.get(name))) != clocks[name]:
                raise BundleValidationError("CONTENT_AS_OF_VIOLATION")
        for item in data["items"]:
            if not isinstance(item, Mapping):
                raise BundleValidationError("CONTENT_SCHEMA_INVALID")
            _must_not_be_future(item, "available_at", clocks["availability_as_of"])
            _must_not_be_future(item, "known_at", clocks["knowledge_as_of"])
            _must_not_be_future(item, "business_at", clocks["business_as_of"])
            for evidence in item.get("evidence", item.get("evidences", [])):
                if isinstance(evidence, Mapping):
                    _must_not_be_future(evidence, "available_at", clocks["availability_as_of"])
                    _must_not_be_future(evidence, "known_at", clocks["knowledge_as_of"])

    @staticmethod
    def _validate_request_bindings(request: Mapping[str, Any]) -> None:
        if request.get("minimum_support_status") != MINIMUM_SUPPORT_STATUS or request.get("policy") != PUBLIC_STRICT or request.get("policy_version") != KNOWLEDGE_POLICY_VERSION:
            raise BundleValidationError("CONTENT_CONTRACT_MISMATCH")
        snapshot = request.get("content_snapshot_id")
        if not isinstance(snapshot, str) or not snapshot.strip() or snapshot.strip().casefold() in {"latest", "current", "default"}:
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        if not isinstance(request.get("max_items"), int) or isinstance(request.get("max_items"), bool) or not 1 <= request["max_items"] <= 100:
            raise BundleValidationError("CONTENT_SCHEMA_INVALID")
        if not all(isinstance(request.get(name), str) and request[name].strip() for name in ("query", "symbol")):
            raise BundleValidationError("CONTENT_SCHEMA_INVALID")
        for name in ("business_as_of", "knowledge_as_of", "availability_as_of"):
            _parse_time(str(request[name]))

    def _validate_items(self, data: Mapping[str, Any]) -> None:
        items = data["items"]
        if not isinstance(items, list) or not items:
            raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        identifiers: set[str] = set()
        evidence_owners: dict[str, str] = {}
        rows: list[tuple[Mapping[str, Any], str, list[Mapping[str, Any]]]] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise BundleValidationError("CONTENT_SCHEMA_INVALID")
            knowledge_id = item.get("knowledge_id") or item.get("id")
            if not isinstance(knowledge_id, str) or not knowledge_id.strip() or knowledge_id in identifiers:
                raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
            identifiers.add(knowledge_id)
            refs = item.get("evidence_refs")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
            by_id = {row.get("evidence_id", row.get("id")): row for row in evidence if isinstance(row, Mapping)}
            if len(by_id) != len(evidence) or any(not isinstance(identifier, str) and identifier is not None for identifier in by_id):
                raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
            if refs is not None and (not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in by_id for ref in refs)):
                raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
            selected = [by_id[ref] for ref in refs] if refs is not None else list(by_id.values())
            for row in selected:
                owner = row.get("knowledge_id", row.get("owner_knowledge_id"))
                if owner is not None and owner != knowledge_id:
                    raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
                evidence_id = row.get("evidence_id", row.get("id"))
                if isinstance(evidence_id, str) and evidence_owners.setdefault(evidence_id, knowledge_id) != knowledge_id:
                    raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
                url = row.get("canonical_url")
                if url is not None:
                    _safe_url(url)
            _reject_secrets(item)
            rows.append((item, knowledge_id, selected))
        # Ownership has been established.  Only then apply support/lifecycle/quality policy.
        for item, _knowledge_id, evidence in rows:
            if item.get("support_status") not in {"SOURCE_SUPPORTED", "CROSS_MODAL_SUPPORTED"} or item.get("lifecycle_status", item.get("lifecycle")) != "ACTIVE" or item.get("grounding_status", "GROUNDED" if item.get("grounded") is True else None) != "GROUNDED":
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
            if item.get("claim_schema_version") not in {None, "claim.atomic.v1"} or item.get("legacy_grounding_incomplete") is True:
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
            if not any(row.get("ownership") == "PRIMARY" or row.get("is_primary") is True or row.get("primary_evidence") is True or row.get("evidence_role") == "PRIMARY" for row in evidence):
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        quality = data.get("quality")
        if not isinstance(quality, Mapping) or quality.get("knowledge_count") != len(items) or not isinstance(quality.get("warnings"), list):
            raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        for key in ("grounded_ratio", "numeric_grounded_ratio"):
            value = quality.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        source = data.get("source")
        if not isinstance(source, Mapping):
            raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
        if source.get("canonical_url") is not None:
            _safe_url(source["canonical_url"])
        _reject_secrets(data)


def _as_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z").replace(".000000Z", "Z")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BundleValidationError("CONTENT_AS_OF_VIOLATION") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BundleValidationError("CONTENT_AS_OF_VIOLATION")
    return parsed.astimezone(UTC)


def _must_not_be_future(row: Mapping[str, Any], key: str, limit: datetime) -> None:
    if key in row and _parse_time(str(row[key])) > limit:
        raise BundleValidationError("CONTENT_AS_OF_VIOLATION")


def _safe_url(value: Any) -> None:
    if not isinstance(value, str):
        raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    try:
        ip_is_private = not ipaddress.ip_address(host).is_global
    except ValueError:
        ip_is_private = host.casefold() == "localhost" or host.casefold().endswith(".localhost")
    if parsed.scheme != "https" or not host or parsed.query or parsed.fragment or parsed.username or parsed.password or ip_is_private:
        raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")


def _reject_secrets(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _SECRET.search(key):
                raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
            if isinstance(key, str) and _UNSAFE_CONTENT_KEY.search(key):
                raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
            _reject_secrets(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_secrets(item)

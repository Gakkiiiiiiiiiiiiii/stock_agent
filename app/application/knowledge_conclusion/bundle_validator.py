"""Fail-closed validation for frozen content knowledge bundle v1 and v2."""
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
    CONTENT_KNOWLEDGE_V2_C14N_VERSION,
    CONTENT_KNOWLEDGE_V2_CONTRACT,
    CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM,
    CONTENT_KNOWLEDGE_V2_SCHEMA_VERSION,
    KNOWLEDGE_POLICY_VERSION,
    MINIMUM_SUPPORT_STATUS,
    PUBLIC_STRICT,
    ContentKnowledgeBundle,
    KnowledgeBundleRequest,
)
from contracts.immutable import freeze

_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_PATH = _ROOT / "contracts" / "fixtures" / "content-knowledge-bundle.v1.json"
_V2_SCHEMA_PATH = _ROOT / "contracts" / "fixtures" / "content-knowledge-bundle.v2.json"
_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT")
_SECRET = re.compile(r"(?:^|_)(?:authorization|token|secret|api_key|password|cookie)(?:$|_)", re.IGNORECASE)
_UNSAFE_CONTENT_KEY = re.compile(r"(?:cookie|header|storage|signed|prompt|raw_response|pii)", re.IGNORECASE)
_SET_ARRAYS = frozenset({"reason_codes", "warnings", "evidence_refs", "evidence_ids"})
_NUMERIC_CLAIM = re.compile(r"\d+(?:\.\d+)?(?:\s*[%％]|\s*(?:万亿|亿|万|日|月|年|bps|倍|EFLOPS))?")
_DETAIL_NORMALIZE = re.compile(r"\s+")


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
        v2_bytes = _V2_SCHEMA_PATH.read_bytes()
        v2_checksum = "sha256:" + hashlib.sha256(v2_bytes).hexdigest().upper()
        if v2_checksum != CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM:
            raise RuntimeError("CONTENT_V2_SCHEMA_LOCK_INVALID")
        self._v2_schema = json.loads(v2_bytes)
        self._v2_schema_validator = jsonschema.Draft202012Validator(self._v2_schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)

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
        payload = {
            "content_snapshot_id": request.content_snapshot_id.strip(), "query": request.query.strip(), "symbol": request.symbol.strip(),
            "business_as_of": _as_z(request.business_as_of), "knowledge_as_of": _as_z(request.knowledge_as_of),
            "availability_as_of": _as_z(request.availability_as_of), "minimum_support_status": MINIMUM_SUPPORT_STATUS,
            "max_items": request.max_items, "policy": PUBLIC_STRICT, "policy_version": KNOWLEDGE_POLICY_VERSION,
        }
        contract_version = getattr(request, "contract_version", CONTENT_KNOWLEDGE_CONTRACT)
        if contract_version == CONTENT_KNOWLEDGE_V2_CONTRACT:
            payload["contract_version"] = CONTENT_KNOWLEDGE_V2_CONTRACT
            payload["subject_scope"] = "ALL_SUBJECTS" if request.symbol.strip().upper() == "UNSPECIFIED" else "SUBJECT_ONLY"
        elif contract_version != CONTENT_KNOWLEDGE_CONTRACT:
            raise BundleValidationError("CONTENT_CONTRACT_MISMATCH")
        return payload

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
        contract = data.get("contract")
        locked = {
            CONTENT_KNOWLEDGE_CONTRACT: (CONTENT_KNOWLEDGE_SCHEMA_VERSION, CONTENT_KNOWLEDGE_C14N_VERSION, CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM),
            CONTENT_KNOWLEDGE_V2_CONTRACT: (CONTENT_KNOWLEDGE_V2_SCHEMA_VERSION, CONTENT_KNOWLEDGE_V2_C14N_VERSION, CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM),
        }.get(contract)
        if locked is None or data.get("schema_version") != locked[0] or data.get("canonicalization_version") != locked[1]:
            raise BundleValidationError("CONTENT_CONTRACT_MISMATCH")
        producer = data.get("producer")
        if not isinstance(producer, Mapping) or producer.get("service") != "stock_content":
            raise BundleValidationError("CONTENT_CONTRACT_MISMATCH")
        if producer.get("contract_checksum") != locked[2]:
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
        if data.get("contract") == CONTENT_KNOWLEDGE_V2_CONTRACT:
            return self._validate_v2(data, expected=expected)
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

    def _validate_v2(self, data: Mapping[str, Any], *, expected: KnowledgeBundleRequest | None) -> ContentKnowledgeBundle:
        """Validate the reviewed multi-topic v2 wire shape without weakening v1.

        v2 is intentionally opt-in.  It is stricter around occurrence review,
        immutable multi-modal evidence, semantic attribution, and quality
        arithmetic because those are the fields an agent needs to avoid
        turning a video opinion or a disputed OCR number into an investment
        fact.
        """
        self.verify_integrity(data)
        try:
            self._v2_schema_validator.validate(data)
        except jsonschema.ValidationError as exc:
            raise BundleValidationError("CONTENT_SCHEMA_INVALID") from exc
        producer = data.get("producer")
        request = data.get("request")
        if not isinstance(producer, Mapping) or not isinstance(request, Mapping):
            raise BundleValidationError("CONTENT_SCHEMA_INVALID")
        expected_fields = {
            "content_snapshot_id", "query", "symbol", "business_as_of", "knowledge_as_of", "availability_as_of",
            "minimum_support_status", "max_items", "policy", "policy_version", "contract_version", "subject_scope",
        }
        if set(request) != expected_fields or request.get("contract_version") != CONTENT_KNOWLEDGE_V2_CONTRACT:
            raise BundleValidationError("CONTENT_SCHEMA_INVALID")
        self._validate_request_bindings(request)
        if request.get("subject_scope") not in {"SUBJECT_ONLY", "ALL_SUBJECTS"}:
            raise BundleValidationError("CONTENT_SCHEMA_INVALID")
        expected_scope = "ALL_SUBJECTS" if str(request["symbol"]).strip().upper() == "UNSPECIFIED" else "SUBJECT_ONLY"
        if request["subject_scope"] != expected_scope:
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        scope = data.get("scope")
        if not isinstance(scope, Mapping) or scope.get("subject_scope") != expected_scope:
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        if (expected_scope == "ALL_SUBJECTS") != (scope.get("requested_subject") is None):
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        if expected_scope == "SUBJECT_ONLY" and scope.get("requested_subject") != request["symbol"]:
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        if data.get("request_hash") != self.request_hash(request):
            raise BundleValidationError("CONTENT_BUNDLE_TAMPERED")
        if expected is not None:
            if expected.contract_version != CONTENT_KNOWLEDGE_V2_CONTRACT:
                raise BundleValidationError("CONTENT_CONTRACT_MISMATCH")
            if data.get("request_hash") != self.request_hash(self.request_payload(expected)):
                raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        if data.get("content_snapshot_id") != request["content_snapshot_id"] or data.get("query") != request["query"]:
            raise BundleValidationError("CONTENT_SNAPSHOT_MISMATCH")
        self._validate_v2_pit(data, request)
        self._validate_v2_items(data)
        self._validate_v2_quality(data)
        _reject_secrets(data)
        frozen = freeze(data)
        return ContentKnowledgeBundle(
            bundle_id=str(data["bundle_id"]), bundle_hash=str(data["bundle_hash"]), request_hash=str(data["request_hash"]),
            content_snapshot_id=str(data["content_snapshot_id"]), producer_sha=str(producer["git_commit"]),
            contract_checksum=CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM, payload=MappingProxyType(frozen),
        )

    def _validate_v2_pit(self, data: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        clocks = {name: _parse_time(str(request[name])) for name in ("business_as_of", "knowledge_as_of", "availability_as_of")}
        for name, clock in clocks.items():
            if _parse_time(str(data.get(name))) != clock:
                raise BundleValidationError("CONTENT_AS_OF_VIOLATION")
        for item in data["items"]:
            temporal = item.get("temporal") if isinstance(item, Mapping) else None
            if not isinstance(temporal, Mapping):
                raise BundleValidationError("CONTENT_SCHEMA_INVALID")
            kind = temporal.get("kind")
            if kind == "UNKNOWN":
                if temporal.get("explicitly_unknown") is not True or any(temporal.get(key) is not None for key in ("start", "end", "as_of", "rule")):
                    raise BundleValidationError("CONTENT_AS_OF_VIOLATION")
            else:
                if temporal.get("explicitly_unknown"):
                    raise BundleValidationError("CONTENT_AS_OF_VIOLATION")
                # An event/as-of assertion cannot be learned after the frozen
                # availability clock.  A forecast target may be later, but it
                # remains a forecast through its claim nature/attribution.
                for key in ("start", "end", "as_of"):
                    value = temporal.get(key)
                    if value is not None and kind != "FORECAST_TARGET" and _parse_time(str(value)) > clocks["availability_as_of"]:
                        raise BundleValidationError("CONTENT_AS_OF_VIOLATION")

    def _validate_v2_items(self, data: Mapping[str, Any]) -> None:
        seen_knowledge: set[str] = set()
        seen_evidence: set[str] = set()
        for item in data["items"]:
            if not isinstance(item, Mapping):
                raise BundleValidationError("CONTENT_SCHEMA_INVALID")
            knowledge_id = item.get("knowledge_id")
            if not isinstance(knowledge_id, str) or not knowledge_id or knowledge_id in seen_knowledge:
                raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
            seen_knowledge.add(knowledge_id)
            if str((item.get("subject") or {}).get("key", "")).upper() == "UNSPECIFIED":
                raise BundleValidationError("CONTENT_SCHEMA_INVALID")
            nature = item.get("claim_nature")
            attribution = item.get("attribution")
            if not isinstance(attribution, Mapping):
                raise BundleValidationError("CONTENT_SCHEMA_INVALID")
            if nature in {"OPINION", "FORECAST", "CAUSAL_THESIS", "SOURCE_OPINION", "SOURCE_FORECAST"} and (
                attribution.get("attributed") is not True
                or not isinstance(attribution.get("source_label"), str)
                or not attribution["source_label"].strip()
            ):
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
            if (
                item.get("source_grade") in {"SECONDARY", "UNKNOWN"}
                and item.get("external_truth_status") != "EXTERNALLY_VERIFIED"
                and (
                    attribution.get("attributed") is not True
                    or not isinstance(attribution.get("source_label"), str)
                    or not attribution["source_label"].strip()
                )
            ):
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
            detail = item.get("detail")
            detail_values = [value for value in detail.values() if isinstance(value, str) and value.strip()] if isinstance(detail, Mapping) else []
            if not detail_values or all(_detail_equivalent(value, str(item.get("statement") or "")) for value in detail_values):
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
            review = item.get("occurrence_review")
            if not isinstance(review, Mapping) or not isinstance(review.get("reason_codes"), list):
                raise BundleValidationError("CONTENT_SCHEMA_INVALID")
            if review.get("status") == "HUMAN_REVIEW_REQUIRED" or item.get("contradiction_group_id"):
                # Review/conflict rows belong to the producer's audit quality
                # ledger, never to an ACTIVE public-strict conclusion bundle.
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
            has_primary_transcript = False
            modalities: set[str] = set()
            for row in evidence:
                if not isinstance(row, Mapping):
                    raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
                evidence_id = row.get("evidence_id")
                if not isinstance(evidence_id, str) or not evidence_id or evidence_id in seen_evidence:
                    raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
                seen_evidence.add(evidence_id)
                if row.get("ownership") == "PRIMARY" and row.get("modality") == "transcript":
                    has_primary_transcript = True
                modality = row.get("modality")
                modalities.add(str(modality))
                locator = row.get("locator")
                if not isinstance(locator, Mapping) or not isinstance(locator.get("start_ms"), int) or not isinstance(locator.get("end_ms"), int) or locator["end_ms"] < locator["start_ms"]:
                    raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
                if modality in {"frame", "ocr", "vision"} and (
                    not isinstance(locator.get("frame_id"), str)
                    or not locator["frame_id"].strip()
                    or not isinstance(row.get("artifact_hash"), str)
                ):
                    raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
                if modality in {"ocr", "vision"}:
                    model = row.get("model")
                    confidence = model.get("confidence") if isinstance(model, Mapping) else None
                    if (
                        not isinstance(model, Mapping)
                        or not all(isinstance(model.get(key), str) and model[key].strip() for key in ("name", "version"))
                        or not isinstance(confidence, (int, float))
                        or isinstance(confidence, bool)
                        or not math.isfinite(float(confidence))
                        or not 0 <= float(confidence) <= 1
                    ):
                        raise BundleValidationError("CONTENT_EVIDENCE_REFERENCE_INVALID")
            if item.get("support_status") not in {"SOURCE_SUPPORTED", "CROSS_MODAL_SUPPORTED"} or not has_primary_transcript:
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
            if item.get("support_status") == "CROSS_MODAL_SUPPORTED" and not modalities.intersection({"frame", "ocr", "vision"}):
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")

    def _validate_v2_quality(self, data: Mapping[str, Any]) -> None:
        quality = data.get("quality")
        items = data.get("items")
        if not isinstance(quality, Mapping) or not isinstance(items, list) or not isinstance(quality.get("warnings"), list):
            raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        count_keys = (
            "candidate_count", "eligible_candidate_count", "excluded_candidate_count", "truncated_candidate_count",
            "knowledge_count", "grounded_count", "numeric_candidate_count", "numeric_grounded_count",
            "human_review_required_count", "conflict_count", "secondary_only_count",
            "external_truth_not_checked_count",
        )
        if any(not isinstance(quality.get(key), int) or isinstance(quality.get(key), bool) or quality[key] < 0 for key in count_keys):
            raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        if (
            quality["knowledge_count"] != len(items)
            or quality["candidate_count"] != quality["eligible_candidate_count"] + quality["excluded_candidate_count"]
            or quality["eligible_candidate_count"] != quality["knowledge_count"] + quality["truncated_candidate_count"]
        ):
            raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        if (
            quality["grounded_count"] > quality["knowledge_count"]
            or quality["numeric_candidate_count"] > quality["knowledge_count"]
            or quality["numeric_grounded_count"] > quality["numeric_candidate_count"]
            or any(quality[key] > quality["candidate_count"] for key in (
                "human_review_required_count", "conflict_count", "secondary_only_count", "external_truth_not_checked_count",
            ))
        ):
            raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        for ratio, numerator, denominator in (("grounded_ratio", "grounded_count", "knowledge_count"), ("numeric_grounded_ratio", "numeric_grounded_count", "numeric_candidate_count")):
            actual = quality.get(ratio)
            expected = 0.0 if quality[denominator] == 0 else quality[numerator] / quality[denominator]
            if not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isfinite(float(actual)) or not math.isclose(float(actual), expected, rel_tol=0.0, abs_tol=1e-12):
                raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        if (quality["human_review_required_count"] or quality["conflict_count"]) and not quality["excluded_candidate_count"]:
            raise BundleValidationError("CONTENT_QUALITY_REJECTED")
        # Ratios must reconcile to the post-publication, max-items-limited
        # rows.  ``source_grade``/``external_truth_status`` are independent
        # from ownership of the course occurrence: a secondary unchecked fact
        # may be preserved with attribution, but never counted as grounded.
        qualified = [item for item in items if _v2_public_qualified(item)]
        numeric_qualified = [item for item in qualified if _v2_numeric(item)]
        grounded = [item for item in qualified if _v2_externally_grounded(item)]
        numeric_grounded = [item for item in numeric_qualified if _v2_externally_grounded(item)]
        if (
            len(qualified) != len(items)
            or quality["grounded_count"] != len(grounded)
            or quality["numeric_candidate_count"] != len(numeric_qualified)
            or quality["numeric_grounded_count"] != len(numeric_grounded)
        ):
            raise BundleValidationError("CONTENT_QUALITY_REJECTED")

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


def _v2_public_qualified(item: Mapping[str, Any]) -> bool:
    review = item.get("occurrence_review")
    evidence = item.get("evidence")
    return (
        item.get("support_status") in {"SOURCE_SUPPORTED", "CROSS_MODAL_SUPPORTED"}
        and item.get("lifecycle_status") == "ACTIVE"
        and item.get("grounding_status") == "GROUNDED"
        and isinstance(review, Mapping)
        and review.get("status") != "HUMAN_REVIEW_REQUIRED"
        and not review.get("reason_codes")
        and not item.get("contradiction_group_id")
        and isinstance(evidence, list)
        and any(row.get("ownership") == "PRIMARY" and row.get("modality") == "transcript" for row in evidence if isinstance(row, Mapping))
    )


def _v2_externally_grounded(item: Mapping[str, Any]) -> bool:
    return (
        item.get("source_grade") == "PRIMARY"
        and item.get("external_truth_status") == "EXTERNALLY_VERIFIED"
    )


def _detail_equivalent(value: str, statement: str) -> bool:
    return _DETAIL_NORMALIZE.sub(" ", value).strip().casefold() == _DETAIL_NORMALIZE.sub(" ", statement).strip().casefold()


def _v2_numeric(item: Mapping[str, Any]) -> bool:
    object_value = (item.get("object") or {}).get("value") if isinstance(item.get("object"), Mapping) else None
    return isinstance(object_value, (int, float)) and not isinstance(object_value, bool) or bool(_NUMERIC_CLAIM.search(str(item.get("statement") or "")))


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

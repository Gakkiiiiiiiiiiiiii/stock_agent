"""Offline consumer lock for SA-02; no Content/Quant/Factor service is contacted."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.adapters.http.content_knowledge_client import (
    ContentBundleClientError,
    RemoteContentKnowledgeBundleClient,
)
from app.application.knowledge_conclusion.bundle_validator import (
    BundleValidationError,
    ContentKnowledgeBundleValidator,
    canonical_json,
)
from app.model_gateway.metrics import TraceContext
from app.model_gateway.retry import RetryPolicy
from app.ports.content_knowledge import (
    CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM,
    KNOWLEDGE_POLICY_VERSION,
    MINIMUM_SUPPORT_STATUS,
    KnowledgeBundleRequest,
)

NOW = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)
GOLDEN_FIXTURE_SHA256 = "8416B05710C932908B2DDD52AF0DD2BCDC11D5802D56EC2CDBA9070887D086B6"


def _request() -> KnowledgeBundleRequest:
    return KnowledgeBundleRequest("snapshot-1", "贵州茅台营收", "600519.SH", NOW, NOW, NOW, max_items=2)


def _payload() -> dict[str, object]:
    validator = ContentKnowledgeBundleValidator()
    request = validator.request_payload(_request())
    payload: dict[str, object] = {
        "contract": "content-knowledge-bundle.v1", "schema_version": "1.0.0", "canonicalization_version": "content-bundle-c14n-v1",
        "request": request, "request_hash": validator.request_hash(request), "content_snapshot_id": "snapshot-1", "query": "贵州茅台营收",
        "source": {"source_type": "bilibili", "source_identity_hash": "source-1", "source_version_id": "version-1", "canonical_url": "https://example.com/report", "source_content_hash": "content-1"}, "business_as_of": "2026-09-06T00:00:00Z", "knowledge_as_of": "2026-09-06T00:00:00Z", "availability_as_of": "2026-09-06T00:00:00Z",
        "items": [{"knowledge_id": "k-1", "claim_id": "claim-1", "occurrence_id": "occurrence-1", "statement": "营收增长", "subject": {"type": "EQUITY", "key": "600519.SH"}, "predicate": "revenue_growth", "object": {"value": 12, "unit": "%"}, "temporal": {"target_start": "2026-09-01T00:00:00Z", "target_end": "2026-09-30T00:00:00Z", "precision": "MONTH"}, "support_status": "SOURCE_SUPPORTED", "lifecycle_status": "ACTIVE", "grounding_status": "GROUNDED", "verification": {"status": "VERIFIED", "reason_codes": []},
                   "evidence": [{"evidence_id": "e-1", "ownership": "PRIMARY", "artifact_id": "artifact-1", "segment_id": "segment-1", "start_ms": 1, "end_ms": 2, "quote": "营收增长", "quote_hash": "quote-hash", "modality": "TRANSCRIPT"}]}],
        "quality": {"knowledge_count": 1, "grounded_ratio": 1.0, "numeric_grounded_ratio": 1, "warnings": []},
        "producer": {"service": "stock_content", "service_version": "1", "git_commit": "producer-sha", "pipeline_version": "1", "contract_checksum": CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM},
    }
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    payload["bundle_id"] = "ckb_" + digest
    payload["bundle_hash"] = "sha256:" + digest
    return payload


def test_vendored_schema_fixture_and_c14n_vectors_are_locked() -> None:
    root = Path(__file__).resolve().parents[2]
    schema = (root / "contracts" / "fixtures" / "content-knowledge-bundle.v1.json").read_bytes()
    assert "sha256:" + hashlib.sha256(schema).hexdigest().upper() == CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM
    fixture_bytes = (root / "contracts" / "fixtures" / "content-knowledge-bundle.c14n-v1.json").read_bytes()
    assert hashlib.sha256(fixture_bytes).hexdigest().upper() == GOLDEN_FIXTURE_SHA256
    fixture = json.loads(fixture_bytes)
    for vector in fixture["vectors"][:3]:
        assert canonical_json(vector["left"]) == canonical_json(vector["right"])
    assert canonical_json({"items": [{"knowledge_id": "z", "subject": {"key": "2", "type": "EQUITY"}}, {"knowledge_id": "a", "subject": {"key": "1", "type": "EQUITY"}}], "warnings": ["z", "a"]}) == b'{"items":[{"knowledge_id":"a","subject":{"key":"1","type":"EQUITY"}},{"knowledge_id":"z","subject":{"key":"2","type":"EQUITY"}}],"warnings":["a","z"]}'
    assert canonical_json(fixture["vectors"][-1]["left"]) != canonical_json(fixture["vectors"][-1]["right"])
    with pytest.raises(BundleValidationError):
        canonical_json({"number": float("nan")})


def test_validator_binds_full_request_hashes_and_freezes_payload() -> None:
    validator = ContentKnowledgeBundleValidator()
    bundle = validator.validate(_payload(), expected=_request())
    assert bundle.bundle_id.startswith("ckb_") and bundle.contract_checksum == CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM
    with pytest.raises(TypeError):
        bundle.payload["query"] = "tamper"  # type: ignore[index]
    with pytest.raises(TypeError):
        validator.freeze_for_conclusion(bundle).payload["query"] = "tamper"


@pytest.mark.parametrize("mutate,code", [
    (lambda payload: payload["request"].update({"symbol": "000001.SZ"}), "CONTENT_BUNDLE_TAMPERED"),
    (lambda payload: payload.update({"availability_as_of": "2026-09-07T00:00:00Z"}), "CONTENT_BUNDLE_TAMPERED"),
    (lambda payload: payload["items"][0]["evidence"].__setitem__(0, {"evidence_id": "e-1", "knowledge_id": "other", "ownership": "PRIMARY"}), "CONTENT_BUNDLE_TAMPERED"),
])
def test_semantic_tamper_fails_before_consumer_use(mutate, code) -> None:
    payload = _payload()
    mutate(payload)
    with pytest.raises(BundleValidationError) as error:
        ContentKnowledgeBundleValidator().validate(payload, expected=_request())
    assert error.value.code == code


def test_request_pit_quality_url_and_secret_invariants_fail_closed() -> None:
    validator = ContentKnowledgeBundleValidator()
    with pytest.raises(BundleValidationError, match="SNAPSHOT"):
        validator.request_payload(KnowledgeBundleRequest("latest", "q", "600519.SH", NOW, NOW, NOW))
    payload = _payload()
    payload["items"][0]["evidence"][0]["available_at"] = (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    _rehash(payload)
    with pytest.raises(BundleValidationError, match="SCHEMA"):
        validator.validate(payload, expected=_request())
    payload = _payload()
    payload["items"][0]["evidence"][0]["canonical_url"] = "https://127.0.0.1/a?token=x"
    _rehash(payload)
    with pytest.raises(BundleValidationError, match="SCHEMA"):
        validator.validate(payload, expected=_request())
    with pytest.raises(BundleValidationError, match="CONTRACT"):
        validator.request_payload(KnowledgeBundleRequest("snapshot-1", "q", "600519.SH", NOW, NOW, NOW, minimum_support_status="PUBLIC_STRICT"))
    payload = _payload()
    payload["items"][0]["api_key"] = "never-accepted"
    _rehash(payload)
    with pytest.raises(BundleValidationError, match="SCHEMA"):
        validator.validate(payload, expected=_request())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["producer"].update({"contract_checksum": "sha256:CC9C3D52602D83A7D77800F4E18162D2066D727E27D3925A0A8789181929D198"}),
        lambda payload: payload["items"][0]["evidence"][0].pop("quote_hash"),
        lambda payload: payload["items"][0]["object"].update({"value": {"nested": "forbidden"}}),
    ],
)
def test_final_producer_lock_rejects_old_checksum_missing_quote_hash_and_nested_object_value(mutate) -> None:
    payload = _payload()
    mutate(payload)
    _rehash(payload)
    with pytest.raises(BundleValidationError):
        ContentKnowledgeBundleValidator().validate(payload, expected=_request())


def test_fixed_routes_auth_redaction_retry_and_typed_errors(tmp_path) -> None:
    key = tmp_path / "content.key"
    key.write_text("secret-not-for-logs", encoding="utf-8")
    calls: list[tuple[str, str, dict]] = []
    responses = [httpx.Response(503), httpx.Response(200, headers={"Content-Type": "application/json"}, json=_payload())]

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return responses.pop(0)

    client = RemoteContentKnowledgeBundleClient("https://content.example", api_key_file=key, request_fn=request, sleeper=lambda _: None, retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0))
    assert client.create_bundle(_request(), trace=TraceContext(trace_id="trace-1")).content_snapshot_id == "snapshot-1"
    assert [(method, url) for method, url, _ in calls] == [("POST", "https://content.example/v1/content/knowledge-bundles")] * 2
    assert calls[0][2]["headers"]["X-Caller-Service"] == "stock_agent"
    assert calls[0][2]["json"]["minimum_support_status"] == MINIMUM_SUPPORT_STATUS
    assert calls[0][2]["json"]["policy"] == "PUBLIC_STRICT"
    assert calls[0][2]["json"]["policy_version"] == KNOWLEDGE_POLICY_VERSION
    assert "secret-not-for-logs" not in repr(client) and calls[0][2]["headers"]["Authorization"] == "Bearer secret-not-for-logs"
    bad = RemoteContentKnowledgeBundleClient("https://content.example", api_key_file=key, request_fn=lambda *args, **kwargs: httpx.Response(415))
    with pytest.raises(ContentBundleClientError) as error:
        bad.get_bundle("ckb_" + "a" * 64, trace=TraceContext(trace_id="trace-1"))
    assert error.value.code == "CONTENT_CONTRACT_MISMATCH"
    swapped = RemoteContentKnowledgeBundleClient(
        "https://content.example", api_key_file=key,
        request_fn=lambda *args, **kwargs: httpx.Response(200, headers={"Content-Type": "application/json"}, json=_payload()),
    )
    with pytest.raises(ContentBundleClientError, match="TAMPERED"):
        swapped.get_bundle("ckb_" + "a" * 64, trace=TraceContext(trace_id="trace-1"))


def test_conclusion_bundle_modules_never_call_or_wrap_browse_search() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = (root / "app" / "ports" / "content_knowledge.py", root / "app" / "adapters" / "http" / "content_knowledge_client.py", root / "app" / "application" / "knowledge_conclusion" / "bundle_validator.py")
    assert all("search_video_knowledge" not in source.read_text(encoding="utf-8") and "/knowledge/search" not in source.read_text(encoding="utf-8") for source in sources)


def _rehash(payload: dict[str, object]) -> None:
    material = {key: value for key, value in payload.items() if key not in {"bundle_id", "bundle_hash"}}
    digest = hashlib.sha256(canonical_json(material)).hexdigest()
    payload["bundle_id"] = "ckb_" + digest
    payload["bundle_hash"] = "sha256:" + digest

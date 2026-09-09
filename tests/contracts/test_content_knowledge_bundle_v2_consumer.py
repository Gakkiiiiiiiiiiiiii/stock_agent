"""Golden v2 consumer cases for reviewed multimodal content knowledge."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.knowledge_conclusion.bundle_validator import (
    BundleValidationError,
    ContentKnowledgeBundleValidator,
    canonical_json,
)
from app.application.knowledge_conclusion.deterministic_fallback import (
    aggregate_bundle_findings,
)
from app.application.knowledge_conclusion.grounding import (
    GroundingError,
    ground_finding,
)
from app.domain.knowledge_conclusion import Finding
from app.ports.content_knowledge import (
    CONTENT_KNOWLEDGE_V2_CONTRACT,
    CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM,
    KnowledgeBundleRequest,
)

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _request() -> KnowledgeBundleRequest:
    return KnowledgeBundleRequest("snapshot-v2", "小鹅通课程知识", "UNSPECIFIED", NOW, NOW, NOW, max_items=10, contract_version=CONTENT_KNOWLEDGE_V2_CONTRACT)


def _item(*, cross_modal: bool = True, nature: str = "METHOD") -> dict[str, object]:
    evidence: list[dict[str, object]] = [
        {
            "evidence_id": "ev-transcript", "ownership": "PRIMARY", "modality": "transcript",
            "artifact_id": "asr-1", "artifact_hash": "sha256:" + "a" * 64,
            "locator": {"segment_id": "seg-1", "frame_id": None, "start_ms": 1000, "end_ms": 3000, "bbox": None},
            "content": "单一标的不超过20%，卫星标的不超过5%。",
        }
    ]
    if cross_modal:
        evidence.append({
            "evidence_id": "ev-ocr", "ownership": "PRIMARY", "modality": "ocr",
            "artifact_id": "frame-artifact-1", "artifact_hash": "sha256:" + "b" * 64,
            "locator": {"segment_id": "seg-1", "frame_id": "frame-1", "start_ms": 1000, "end_ms": 3000, "bbox": [0, 0, 100, 40]},
            "content": "单一标的≤20%，卫星≤5%", "model": {"name": "PaddleOCR", "version": "3.7", "confidence": 0.99},
        })
    attribution = {"attributed": False, "source_label": None, "speaker": None}
    temporal = {"kind": "EVENT", "start": "2026-09-01T00:00:00Z", "end": "2026-09-01T00:00:00Z", "as_of": None, "rule": None, "label": None, "precision": "DAY", "explicitly_unknown": False}
    statement = "单一标的仓位不超过20%。"
    if nature == "SOURCE_FORECAST":
        attribution = {"attributed": True, "source_label": "讲者", "speaker": "讲者"}
        temporal = {"kind": "FORECAST_TARGET", "start": None, "end": "2030-12-31T00:00:00Z", "as_of": None, "rule": None, "label": "2030年", "precision": "YEAR", "explicitly_unknown": False}
        statement = "信息基础设施投资规模将扩大。"
    return {
        "knowledge_id": "ku-v2", "claim_id": "claim-v2", "occurrence_id": "occ-v2", "statement": statement,
        "subject": {"type": "DOMAIN", "key": "PORTFOLIO_RISK_MANAGEMENT"}, "predicate": "position_limit", "object": {"value": 20, "unit": "PERCENT"},
        "primary_domain": "PORTFOLIO_RISK_MANAGEMENT", "claim_nature": nature, "attribution": attribution,
        "source_grade": "PRIMARY",
        "detail": {"explanation": "集中度上限限制单一标的风险。", "procedure": "建仓前按总资产计算仓位。", "mechanism": None, "formula": None, "example": None, "scope": None, "risks": "超过上限会放大单一标的损失。"},
        "temporal": temporal, "evidence": evidence, "occurrence_review": {"status": "NOT_REQUIRED", "reason_codes": []},
        "support_status": "CROSS_MODAL_SUPPORTED" if cross_modal else "SOURCE_SUPPORTED", "lifecycle_status": "ACTIVE",
        "verification": {"status": "VERIFIED", "reason_codes": []}, "external_truth_status": "EXTERNALLY_VERIFIED", "grounding_status": "GROUNDED", "confidence": 0.9,
    }


def _payload(*, cross_modal: bool = True, nature: str = "METHOD") -> dict[str, object]:
    request = ContentKnowledgeBundleValidator().request_payload(_request())
    item = _item(cross_modal=cross_modal, nature=nature)
    payload: dict[str, object] = {
        "contract": CONTENT_KNOWLEDGE_V2_CONTRACT, "schema_version": "2.0.0", "canonicalization_version": "content-bundle-c14n-v2",
        "request": request, "request_hash": "", "content_snapshot_id": "snapshot-v2", "query": "小鹅通课程知识",
        "scope": {"subject_scope": "ALL_SUBJECTS", "requested_subject": None},
        "source": {"source_type": "xiaoe", "source_identity_hash": "source-v2", "source_version_id": "version-v2", "canonical_url": "https://example.com/course", "source_content_hash": "source-content-v2"},
        "business_as_of": "2026-09-09T00:00:00Z", "knowledge_as_of": "2026-09-09T00:00:00Z", "availability_as_of": "2026-09-09T00:00:00Z",
        "items": [item],
        "quality": {"candidate_count": 1, "eligible_candidate_count": 1, "excluded_candidate_count": 0, "truncated_candidate_count": 0, "knowledge_count": 1, "grounded_count": 1, "numeric_candidate_count": 1, "numeric_grounded_count": 1, "human_review_required_count": 0, "conflict_count": 0, "secondary_only_count": 0, "external_truth_not_checked_count": 0, "grounded_ratio": 1.0, "numeric_grounded_ratio": 1.0, "warnings": []},
        "producer": {"service": "stock_content", "service_version": "2", "git_commit": "a" * 40, "pipeline_version": "pipeline.v4", "contract_checksum": CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM},
    }
    _rehash(payload)
    return payload


def _rehash(payload: dict[str, object]) -> None:
    material = {key: value for key, value in payload.items() if key not in {"bundle_id", "bundle_hash"}}
    digest = hashlib.sha256(canonical_json(material)).hexdigest()
    payload["request_hash"] = "sha256:" + hashlib.sha256(canonical_json(payload["request"])).hexdigest()
    digest = hashlib.sha256(canonical_json({key: value for key, value in payload.items() if key not in {"bundle_id", "bundle_hash"}})).hexdigest()
    payload["bundle_id"] = "ckb_" + digest
    payload["bundle_hash"] = "sha256:" + digest


def test_v2_golden_bundle_has_locked_identity_multimodal_evidence_and_rich_cited_detail() -> None:
    payload = _payload()
    bundle = ContentKnowledgeBundleValidator().validate(payload, expected=_request())
    assert bundle.contract_checksum == CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM
    findings = aggregate_bundle_findings(dict(bundle.payload))
    assert len(findings) == 1
    assert "集中度上限限制" in findings[0].text
    assert "课程提出" not in findings[0].text


def test_v2_canonicalization_is_order_independent_and_schema_checksum_is_locked() -> None:
    first = _payload()
    second = _payload()
    second["items"][0]["evidence"].reverse()
    _rehash(second)
    assert first["bundle_id"] == second["bundle_id"]
    root = Path(__file__).resolve().parents[2]
    assert "sha256:" + hashlib.sha256((root / "contracts/fixtures/content-knowledge-bundle.v2.json").read_bytes()).hexdigest().upper() == CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM
    upstream = Path(r"D:\project\worktrees\stock_content-EPIC-043\contracts\content-knowledge-bundle.v2.json")
    if upstream.exists():
        assert (root / "contracts/fixtures/content-knowledge-bundle.v2.json").read_bytes() == upstream.read_bytes()


@pytest.mark.parametrize("mutate", [
    lambda payload: payload["items"][0].update({"support_status": "CROSS_MODAL_SUPPORTED", "evidence": payload["items"][0]["evidence"][:1]}),
    lambda payload: payload["items"][0]["evidence"][1].pop("model"),
    lambda payload: payload["items"][0].update({"occurrence_review": {"status": "HUMAN_REVIEW_REQUIRED", "reason_codes": ["OCR_CONFLICT"]}}),
    lambda payload: payload["quality"].update({"grounded_ratio": 1.0, "grounded_count": 0}),
])
def test_v2_rejects_missing_visual_metadata_review_required_and_optimistic_quality(mutate) -> None:
    payload = _payload()
    mutate(payload)
    _rehash(payload)
    with pytest.raises(BundleValidationError):
        ContentKnowledgeBundleValidator().validate(payload, expected=_request())


def test_v2_rejects_reused_evidence_and_payload_tamper() -> None:
    payload = _payload()
    second = deepcopy(payload["items"][0])
    second["knowledge_id"] = "ku-v2-second"
    second["claim_id"] = "claim-v2-second"
    second["occurrence_id"] = "occ-v2-second"
    payload["items"].append(second)
    payload["quality"].update({"candidate_count": 2, "eligible_candidate_count": 2, "knowledge_count": 2, "grounded_count": 2, "numeric_candidate_count": 2, "numeric_grounded_count": 2})
    _rehash(payload)
    with pytest.raises(BundleValidationError, match="EVIDENCE_REFERENCE"):
        ContentKnowledgeBundleValidator().validate(payload)
    payload = _payload()
    payload["items"][0]["statement"] = "单一标的仓位不超过30%。"
    with pytest.raises(BundleValidationError, match="TAMPERED"):
        ContentKnowledgeBundleValidator().validate(payload)


def test_v2_secondary_unchecked_fact_is_visible_but_cannot_make_quality_optimistic() -> None:
    payload = _payload()
    item = payload["items"][0]
    item.update({
        "source_grade": "SECONDARY",
        "external_truth_status": "NOT_CHECKED",
        "attribution": {"attributed": True, "source_label": "财经媒体报道", "speaker": None},
    })
    payload["quality"].update({
        "grounded_count": 0, "numeric_grounded_count": 0,
        "grounded_ratio": 0.0, "numeric_grounded_ratio": 0.0,
        "secondary_only_count": 1, "external_truth_not_checked_count": 1,
    })
    _rehash(payload)
    ContentKnowledgeBundleValidator().validate(payload, expected=_request())

    payload["quality"].update({"grounded_count": 1, "numeric_grounded_count": 1, "grounded_ratio": 1.0, "numeric_grounded_ratio": 1.0})
    _rehash(payload)
    with pytest.raises(BundleValidationError, match="QUALITY_REJECTED"):
        ContentKnowledgeBundleValidator().validate(payload, expected=_request())


def test_v2_secondary_unchecked_fact_requires_attribution_in_bundle_and_conclusion() -> None:
    payload = _payload()
    item = payload["items"][0]
    item.update({"source_grade": "SECONDARY", "external_truth_status": "NOT_CHECKED"})
    _rehash(payload)
    with pytest.raises(BundleValidationError, match="QUALITY_REJECTED"):
        ContentKnowledgeBundleValidator().validate(payload, expected=_request())

    item["attribution"] = {"attributed": True, "source_label": "财经媒体报道", "speaker": None}
    payload["quality"].update({
        "grounded_count": 0, "numeric_grounded_count": 0,
        "grounded_ratio": 0.0, "numeric_grounded_ratio": 0.0,
        "secondary_only_count": 1, "external_truth_not_checked_count": 1,
    })
    _rehash(payload)
    bundle = ContentKnowledgeBundleValidator().validate(payload, expected=_request())
    finding = aggregate_bundle_findings(dict(bundle.payload))[0]
    assert finding.text.startswith("财经媒体报道的观点：")
    unlabelled = Finding(text=item["statement"], knowledge_ids=("ku-v2",), evidence_ids=("ev-transcript",), confidence=0.8)
    with pytest.raises(GroundingError):
        ground_finding(dict(bundle.payload), unlabelled)


def test_v2_quality_requires_published_arithmetic_after_truncation() -> None:
    payload = _payload()
    payload["quality"].update({
        "candidate_count": 3, "eligible_candidate_count": 2, "excluded_candidate_count": 1,
        "truncated_candidate_count": 1,
    })
    _rehash(payload)
    ContentKnowledgeBundleValidator().validate(payload, expected=_request())

    payload["quality"]["truncated_candidate_count"] = 0
    _rehash(payload)
    with pytest.raises(BundleValidationError, match="QUALITY_REJECTED"):
        ContentKnowledgeBundleValidator().validate(payload, expected=_request())


def test_v2_source_forecast_stays_attributed_and_forecast_safe_in_conclusion_grounding() -> None:
    payload = _payload(nature="SOURCE_FORECAST")
    bundle = ContentKnowledgeBundleValidator().validate(payload, expected=_request())
    finding = aggregate_bundle_findings(dict(bundle.payload))[0]
    assert "讲者的预测" in finding.text and "预测目标：2030年" in finding.text
    assert ground_finding(dict(bundle.payload), finding).knowledge[0].knowledge_id == "ku-v2"
    unlabelled = Finding(text="信息基础设施投资规模将扩大。", knowledge_ids=("ku-v2",), evidence_ids=("ev-transcript",), confidence=0.8)
    with pytest.raises(GroundingError):
        ground_finding(dict(bundle.payload), unlabelled)

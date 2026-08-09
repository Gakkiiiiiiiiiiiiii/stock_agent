"""P0-1 / P0-2 / P1-7 回归测试：Evidence 元数据保真、confidence=0.0、knowledge_kind 枚举校验。

对应设计文档 §85 Evidence Fidelity / Confidence 清单。
"""

from __future__ import annotations

from engines.content.knowledge_enums import (
    HIGH_RISK_KINDS,
    LEGACY_SUPPORT_ALIASES,
    LEGACY_TRUTH_ALIASES,
    SUPPORT_ORDER,
    KnowledgeKind,
    SupportStatus,
    support_rank,
)
from engines.content.knowledge_schema import EVIDENCE_FIELDS, KnowledgeUnitSchemaValidator


def _unit(evidence: list[dict], **overrides) -> dict:
    unit = {
        "primary_domain": "MARKET",
        "knowledge_kind": "STATE",
        "expression_type": "AUTHOR_EXPLICIT",
        "subject_type": "THEME",
        "subject_key": "券商",
        "subject_name": "券商",
        "predicate_key": "market_state",
        "statement": "券商板块维持偏强状态。",
        "canonical_statement": "券商板块维持偏强状态。",
        "evidence": evidence,
    }
    unit.update(overrides)
    return unit


def _validate(unit: dict) -> dict:
    return KnowledgeUnitSchemaValidator().validate_one(unit, chapter={})


def test_schema_preserves_raw_normalized_evidence():
    result = _validate(
        _unit(
            [
                {
                    "source_type": "ASR",
                    "evidence_text": "券商板块维持偏强状态。",
                    "raw_text": "券商版块维持偏强状态",
                    "normalized_text": "券商板块维持偏强状态",
                    "correction_trace": [{"type": "DICTIONARY_CORRECTION", "from": "版块", "to": "板块"}],
                }
            ]
        )
    )

    assert result["accepted"] is True
    evidence = result["unit"]["evidence"][0]
    assert evidence["raw_text"] == "券商版块维持偏强状态"
    assert evidence["normalized_text"] == "券商板块维持偏强状态"
    assert evidence["correction_trace"][0]["to"] == "板块"


def test_schema_preserves_word_timestamps():
    word_timestamps = [{"word": "券商", "start_ms": 120, "end_ms": 260}]
    result = _validate(
        _unit(
            [
                {
                    "source_type": "ASR",
                    "evidence_text": "券商板块维持偏强状态。",
                    "word_timestamps": word_timestamps,
                }
            ]
        )
    )

    assert result["accepted"] is True
    assert result["unit"]["evidence"][0]["word_timestamps"] == word_timestamps


def test_schema_preserves_asr_metrics():
    asr_metrics = {"avg_logprob": -0.31, "no_speech_prob": 0.02}
    result = _validate(
        _unit(
            [
                {
                    "source_type": "ASR",
                    "evidence_text": "券商板块维持偏强状态。",
                    "asr_metrics": asr_metrics,
                }
            ]
        )
    )

    assert result["accepted"] is True
    assert result["unit"]["evidence"][0]["asr_metrics"] == asr_metrics


def test_schema_preserves_ocr_bbox():
    bbox = [[10, 20], [110, 20], [110, 60], [10, 60]]
    ocr_metrics = {"mean_confidence": 0.91, "token_count": 12}
    result = _validate(
        _unit(
            [
                {
                    "source_type": "OCR",
                    "evidence_text": "K线图：券商指数站上五日均线",
                    "frame_id": "frame_12",
                    "bbox": bbox,
                    "ocr_metrics": ocr_metrics,
                }
            ]
        )
    )

    assert result["accepted"] is True
    evidence = result["unit"]["evidence"][0]
    assert evidence["bbox"] == bbox
    assert evidence["ocr_metrics"] == ocr_metrics
    assert evidence["frame_id"] == "frame_12"


def test_schema_preserves_speaker_id():
    result = _validate(
        _unit(
            [
                {
                    "source_type": "ASR",
                    "evidence_text": "券商板块维持偏强状态。",
                    "speaker_id": "SPEAKER_01",
                    "speaker_attribution_confidence": 0.87,
                }
            ]
        )
    )

    assert result["accepted"] is True
    evidence = result["unit"]["evidence"][0]
    assert evidence["speaker_id"] == "SPEAKER_01"
    assert evidence["speaker_attribution_confidence"] == 0.87


def test_zero_confidence_remains_zero():
    result = _validate(
        _unit(
            [
                {
                    "source_type": "ASR",
                    "evidence_text": "券商板块维持偏强状态。",
                    "confidence_score": 0.0,
                    "confidence": 0.8,
                }
            ]
        )
    )

    assert result["accepted"] is True
    assert result["unit"]["evidence"][0]["confidence_score"] == 0.0


def test_confidence_fallback_only_when_score_is_none():
    result = _validate(
        _unit(
            [
                {
                    "source_type": "ASR",
                    "evidence_text": "券商板块维持偏强状态。",
                    "confidence": 0.8,
                }
            ]
        )
    )

    assert result["accepted"] is True
    assert result["unit"]["evidence"][0]["confidence_score"] == 0.8


def test_unknown_knowledge_kind_rejected():
    result = _validate(_unit([{"evidence_text": "券商板块维持偏强状态。"}], knowledge_kind="VALUTION"))

    assert result["accepted"] is False
    assert result["reason"] == "unknown_knowledge_kind"


def test_new_knowledge_kinds_accepted():
    for kind in ("VALUATION", "FINANCIAL_METRIC", "PRICE_LEVEL", "POLICY_FACT", "MODEL_INFERENCE"):
        result = _validate(_unit([{"evidence_text": "券商板块维持偏强状态。"}], knowledge_kind=kind))
        assert result["accepted"] is True, kind
        assert result["unit"]["knowledge_kind"] == kind


def test_knowledge_kind_fallback_state_is_valid():
    result = _validate(_unit([{"evidence_text": "券商板块维持偏强状态。"}], knowledge_kind=""))

    assert result["accepted"] is True
    assert result["unit"]["knowledge_kind"] == "STATE"
    assert "filled_knowledge_kind" in result["repairs"]


def test_enum_single_source_of_truth():
    assert {"VALUATION", "FINANCIAL_METRIC", "PRICE_LEVEL", "POLICY_FACT"} <= KnowledgeKind.values()
    assert HIGH_RISK_KINDS <= KnowledgeKind.values()
    assert SUPPORT_ORDER == ("UNSUPPORTED", "SOURCE_LOCATED", "SOURCE_SUPPORTED", "CROSS_MODAL_SUPPORTED")
    assert SupportStatus.values() == set(SUPPORT_ORDER)
    assert LEGACY_TRUTH_ALIASES == {"NOT_EXTERNALLY_VERIFIED": "NOT_CHECKED"}


def test_support_rank_orders_legacy_statuses():
    assert support_rank(None) == 0
    assert support_rank("UNSUPPORTED") == 0
    assert support_rank("SOURCE_LOCATED") < support_rank("NEEDS_REVIEW")
    assert support_rank("NEEDS_REVIEW") < support_rank("SOURCE_SUPPORTED")
    assert support_rank("SOURCE_SUPPORTED") < support_rank("CROSS_MODAL_SUPPORTED")
    assert support_rank("CROSS_MODAL_SUPPORTED") < support_rank("EXTERNALLY_VERIFIED")
    assert support_rank("CROSS_MODAL_SUPPORTED") < support_rank("VALIDATED")
    for legacy, canonical in LEGACY_SUPPORT_ALIASES.items():
        assert support_rank(legacy) >= support_rank(canonical)


def test_evidence_fields_whitelist_covers_extractor_metadata():
    assert {
        "raw_text",
        "normalized_text",
        "speaker_id",
        "speaker_attribution_confidence",
        "word_timestamps",
        "bbox",
        "asr_metrics",
        "ocr_metrics",
        "correction_trace",
        "semantic_support_score",
        "numeric_consistency_score",
        "entity_consistency_score",
    } <= EVIDENCE_FIELDS

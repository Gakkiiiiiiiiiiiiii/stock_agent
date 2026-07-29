from __future__ import annotations

from engines.content.knowledge_schema import KnowledgeUnitSchemaValidator
from engines.content.knowledge_unit_extractor import KnowledgeUnitExtractor
from engines.content.knowledge_unit_normalizer import KnowledgeUnitNormalizer


class InvalidJsonModel:
    def available(self):
        return True

    def complete(self, **kwargs):
        _ = kwargs
        return {"content": "不是 JSON"}


def _chapter(text: str, *, ocr_text: str = "", confidence: float = 0.8) -> dict:
    return {
        "chapter_index": 0,
        "chapter_type": "ANALYSIS",
        "primary_domain": "MARKET",
        "secondary_domains": [],
        "start_ms": 0,
        "end_ms": 60000,
        "confidence_score": confidence,
        "entities": ["券商"],
        "windows": [
            {
                "window_index": 0,
                "start_ms": 0,
                "end_ms": 60000,
                "transcript_text": text,
                "ocr_text": ocr_text,
                "confidence_score": confidence,
            }
        ],
    }


def test_schema_rejects_missing_required_fields_and_empty_evidence():
    validator = KnowledgeUnitSchemaValidator()
    missing_predicate = validator.validate_one(
        {
            "primary_domain": "MARKET",
            "knowledge_kind": "STATE",
            "expression_type": "AUTHOR_EXPLICIT",
            "subject_key": "券商",
            "statement": "券商偏强",
            "canonical_statement": "券商偏强",
            "evidence": [{"evidence_text": "券商偏强"}],
        },
        chapter={},
    )
    missing_evidence = validator.validate_one(
        {
            "primary_domain": "MARKET",
            "knowledge_kind": "STATE",
            "expression_type": "AUTHOR_EXPLICIT",
            "predicate_key": "state",
            "subject_key": "券商",
            "statement": "券商偏强",
            "canonical_statement": "券商偏强",
            "evidence": [],
        },
        chapter={},
    )

    assert missing_predicate["accepted"] is False
    assert missing_predicate["reason"] == "missing_predicate_key"
    assert missing_evidence["accepted"] is False
    assert missing_evidence["reason"] == "missing_evidence"


def test_invalid_llm_json_falls_back_to_rule_extraction_and_records_metrics():
    extractor = KnowledgeUnitExtractor(model_client=InvalidJsonModel())
    units = extractor.extract(
        metadata={"title": "测试", "publish_time": "20260729"},
        chapters=[_chapter("券商处于偏强状态。如果跌破五日线就需要减仓。")],
    )

    assert len(units) >= 2
    assert extractor.last_validation_report["accepted_count"] == len(units)
    assert all(unit["evidence"] for unit in units)
    assert all(unit.get("predicate_key") for unit in units)


def test_ocr_evidence_is_attached_when_statement_mentions_chart():
    extractor = KnowledgeUnitExtractor(model_client=InvalidJsonModel())
    units = extractor.extract(
        metadata={"title": "测试"},
        chapters=[_chapter("图表显示券商价格站上均线，状态偏强。", ocr_text="K线图：券商指数站上五日均线")],
    )

    evidence_types = {item["source_type"] for unit in units for item in unit["evidence"]}
    assert "ASR" in evidence_types
    assert "OCR" in evidence_types


def test_low_evidence_quality_marks_needs_review():
    normalizer = KnowledgeUnitNormalizer()
    units = normalizer.normalize(
        [
            {
                "chapter_index": 0,
                "primary_domain": "MARKET",
                "knowledge_kind": "STATE",
                "temporal_class": "SNAPSHOT",
                "expression_type": "AUTHOR_EXPLICIT",
                "predicate_key": "state",
                "statement": "券商偏强",
                "canonical_statement": "券商偏强",
                "entities": [{"entity_type": "THEME", "entity_key": "券商", "entity_name": "券商", "relation_role": "SUBJECT"}],
                "evidence": [{"source_type": "ASR", "evidence_text": "券商偏强", "confidence_score": 0.2}],
            }
        ],
        metadata={"platform_video_id": "BVTEST"},
    )

    assert units[0]["verification_status"] == "NEEDS_REVIEW"


def test_schema_rejects_subjectless_state_even_with_chapter_domain():
    result = KnowledgeUnitSchemaValidator().validate_one(
        {
            "primary_domain": "MARKET",
            "knowledge_kind": "STATE",
            "expression_type": "AUTHOR_EXPLICIT",
            "predicate_key": "state",
            "statement": "市场偏强",
            "canonical_statement": "市场偏强",
            "evidence": [{"evidence_text": "市场偏强"}],
        },
        chapter={"primary_domain": "MARKET"},
    )

    assert result["accepted"] is False
    assert result["reason"] == "missing_subject"


def test_schema_allows_domain_level_method_with_review_flag():
    result = KnowledgeUnitSchemaValidator().validate_one(
        {
            "primary_domain": "MARKET",
            "knowledge_kind": "METHOD",
            "expression_type": "AUTHOR_EXPLICIT",
            "predicate_key": "method",
            "statement": "判断市场强弱要看量价配合",
            "canonical_statement": "判断市场强弱要看量价配合",
            "evidence": [{"evidence_text": "判断市场强弱要看量价配合"}],
        },
        chapter={"primary_domain": "MARKET"},
    )

    assert result["accepted"] is True
    assert result["unit"]["subject_type"] == "DOMAIN"
    assert result["unit"]["subject_key"] == "MARKET"
    assert result["unit"]["attributes"]["domain_level"] is True
    assert result["unit"]["verification_status"] == "NEEDS_REVIEW"


def test_normalizer_does_not_default_state_subject_to_domain():
    units = KnowledgeUnitNormalizer().normalize(
        [
            {
                "chapter_index": 0,
                "primary_domain": "MARKET",
                "knowledge_kind": "STATE",
                "temporal_class": "SNAPSHOT",
                "expression_type": "AUTHOR_EXPLICIT",
                "predicate_key": "state",
                "statement": "市场偏强",
                "canonical_statement": "市场偏强",
                "evidence": [{"source_type": "ASR", "evidence_text": "市场偏强", "start_ms": 0, "end_ms": 1000}],
            }
        ],
        metadata={"platform_video_id": "BVTEST"},
    )

    assert units == []

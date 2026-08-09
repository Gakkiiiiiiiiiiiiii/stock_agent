from __future__ import annotations

import pytest

from engines.content.knowledge_schema import KnowledgeUnitSchemaValidator
from engines.content.knowledge_unit_extractor import KnowledgeUnitExtractor
from engines.content.knowledge_unit_normalizer import KnowledgeUnitNormalizer


class InvalidJsonModel:
    def available(self):
        return True

    def complete(self, **kwargs):
        _ = kwargs
        return {"content": "不是 JSON"}


class CuratedKnowledgeModel:
    def available(self):
        return True

    def complete(self, **kwargs):
        _ = kwargs
        return {
            "provider": "openai_compatible",
            "model": "k3",
            "content": """{"units": [
              {
                "primary_domain": "MARKET", "knowledge_kind": "STATE", "expression_type": "AUTHOR_EXPLICIT",
                "subject_type": "THEME", "subject_key": "券商", "subject_name": "券商", "predicate_key": "market_state",
                "conclusion": "券商板块维持偏强状态。", "canonical_statement": "券商板块维持偏强状态。",
                "claim_type": "OPINION", "sentiment": "BULLISH", "extraction_confidence": 0.9, "entities": ["券商"],
                "evidence": [{"source_ref": "window_0"}]
              },
              {
                "primary_domain": "MARKET", "knowledge_kind": "FORECAST", "expression_type": "AUTHOR_EXPLICIT",
                "subject_type": "THEME", "subject_key": "券商", "subject_name": "券商", "predicate_key": "market_state",
                "conclusion": "券商板块维持偏强状态。", "canonical_statement": "券商板块维持偏强状态。",
                "claim_type": "FORECAST", "sentiment": "BULLISH", "extraction_confidence": 0.85,
                "evidence": [{"source_ref": "window_0"}]
              },
              {
                "primary_domain": "RISK", "knowledge_kind": "RISK_CONDITION", "expression_type": "AUTHOR_EXPLICIT",
                "subject_type": "THEME", "subject_key": "券商", "subject_name": "券商", "predicate_key": "risk_condition",
                "conclusion": "跌破五日线时应降低仓位。", "canonical_statement": "跌破五日线时应降低仓位。",
                "claim_type": "OPINION", "sentiment": "BEARISH", "extraction_confidence": 0.9,
                "evidence": [{"source_ref": "window_0"}]
              }
            ]}""",
        }


class PartialInvalidJsonModel:
    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def complete(self, **kwargs):
        _ = kwargs
        self.calls += 1
        if self.calls == 1:
            return {
                "content": """{"units":[{"knowledge_kind":"STATE","subject_key":"市场","subject_name":"市场","predicate_key":"market_state","conclusion":"市场维持震荡格局。","canonical_statement":"市场维持震荡格局。","claim_type":"OPINION","sentiment":"NEUTRAL","extraction_confidence":0.9,"evidence":[{"source_ref":"window_0","evidence_text":"市场处于震荡状态"}]}]}"""
            }
        return {"content": "[不合法 JSON"}


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


def test_invalid_llm_json_raises_instead_of_rule_fallback():
    extractor = KnowledgeUnitExtractor(model_client=InvalidJsonModel())
    with pytest.raises(RuntimeError, match="LLM 知识抽取失败"):
        extractor.extract(
            metadata={"title": "测试", "publish_time": "20260729"},
            chapters=[_chapter("券商处于偏强状态。如果跌破五日线就需要减仓。")],
        )


def test_grounding_check_drops_unit_with_fabricated_numbers():
    extractor = KnowledgeUnitExtractor(model_client=CuratedKnowledgeModel())
    chapter = _chapter("消费转向供给侧改革，酿酒板块反弹到压力位。以4000点乐观假设外推，那片矿就能再造一个赤峰黄金。")
    fabricated = {
        "chapter_index": 0,
        "primary_domain": "COMPANY",
        "knowledge_kind": "FACT",
        "expression_type": "AUTHOR_EXPLICIT",
        "predicate_key": "fact",
        "subject_key": "赤峰黄金",
        "statement": "老挝Sepon铜金矿黄金当量由约107吨增至260吨，测算利润或再造一个赤峰黄金。",
        "canonical_statement": "老挝Sepon铜金矿黄金当量由约107吨增至260吨。",
        "entities": [{"entity_name": "赤峰黄金", "entity_type": "SECURITY"}],
        "evidence": [{"source_ref": "window_0"}],
    }

    assert extractor._grounding_ok(fabricated, chapter) is False
    grounded = dict(fabricated)
    grounded["statement"] = "按金价4000的乐观假设线性外推，单片矿利润足以再造一个赤峰黄金体量。"
    assert extractor._grounding_ok(grounded, chapter) is True


def test_extract_raises_when_model_unavailable():
    class UnavailableModel:
        def available(self):
            return False

    extractor = KnowledgeUnitExtractor(model_client=UnavailableModel())
    with pytest.raises(RuntimeError, match="LLM 未配置"):
        extractor.extract(
            metadata={"title": "测试"},
            chapters=[_chapter("券商处于偏强状态。")],
        )


def test_llm_extraction_merges_duplicate_claims_and_keeps_grounded_evidence():
    extractor = KnowledgeUnitExtractor(model_client=CuratedKnowledgeModel())
    units = extractor.extract(
        metadata={"title": "测试", "publish_time": "20260729"},
        chapters=[_chapter("券商板块仍处于偏强状态。如果跌破五日线就需要减仓。")],
    )

    assert len(units) == 2
    assert all(len(unit["statement"]) <= 240 for unit in units)
    assert all(unit["evidence"][0]["start_ms"] == 0 for unit in units)
    assert extractor.last_validation_report["accepted_count"] == 2


def test_failed_fragment_raises_after_retries(monkeypatch):
    monkeypatch.setattr("engines.content.knowledge_unit_extractor.time.sleep", lambda seconds: None)
    chapter = _chapter("市场处于震荡状态。")
    chapter["windows"].append(
        {
            "window_index": 1,
            "start_ms": 60000,
            "end_ms": 120000,
            "transcript_text": "如果跌破五日线就需要减仓。",
            "ocr_text": "",
            "confidence_score": 0.8,
        }
    )
    extractor = KnowledgeUnitExtractor(
        model_client=PartialInvalidJsonModel(),
        max_llm_fragment_chars=12,
    )

    with pytest.raises(RuntimeError, match="fragment=2/2"):
        extractor.extract(metadata={"title": "测试"}, chapters=[chapter])


def test_ocr_evidence_is_attached_when_statement_mentions_chart():
    chapter = _chapter("图表显示券商价格站上均线，状态偏强。", ocr_text="K线图：券商指数站上五日均线")

    evidence = KnowledgeUnitExtractor._evidence_for_sentence("图表显示券商价格站上均线，状态偏强。", chapter)

    evidence_types = {item["source_type"] for item in evidence}
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
                "evidence": [{"source_type": "ASR", "evidence_text": "券商偏强", "start_ms": 0, "end_ms": 1000, "confidence_score": 0.2}],
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


def test_normalizer_keeps_llm_unit_with_own_subject_and_related_entities():
    """回归：LLM 单元自带 subject_key、实体全为 RELATED 且文本无实体词时不得被误杀。"""
    units = KnowledgeUnitNormalizer().normalize(
        [
            {
                "chapter_index": 0,
                "primary_domain": "MARKET",
                "knowledge_kind": "STATE",
                "expression_type": "AUTHOR_EXPLICIT",
                "predicate_key": "deleveraging_state",
                "subject_type": "THEME",
                "subject_key": "市场流动性",
                "subject_name": "市场流动性",
                "statement": "出清接近尾声，被动抛压明显减弱。",
                "canonical_statement": "出清接近尾声，被动抛压明显减弱。",
                "entities": [{"entity_type": "THEME", "entity_key": "动量", "entity_name": "动量", "relation_role": "RELATED"}],
                "evidence": [{"source_type": "ASR", "evidence_text": "出清接近尾声", "start_ms": 0, "end_ms": 1000}],
            }
        ],
        metadata={"platform_video_id": "BVTEST"},
    )

    assert len(units) == 1
    assert units[0]["subject_key"] == "市场流动性"


class _EmptyEntityNormalizer:
    def extract_entities(self, text, transcript, title):
        return []


def test_missing_llm_entity_confidence_remains_none():
    """§16/§17：LLM 未给 confidence 时保持 UNKNOWN（None），不得伪造 0.7。"""
    chapter = _chapter("券商板块偏强。")
    entities = KnowledgeUnitExtractor._normalize_llm_entities(
        [{"entity_name": "宁德时代", "entity_type": "SECURITY", "ticker": "300750"}, "券商"],
        chapter,
    )

    assert [entity["entity_name"] for entity in entities] == ["宁德时代", "券商"]
    assert all(entity["confidence_score"] is None for entity in entities)
    # 无 LLM 实体时回退 chapter 实体，同样不得伪造默认值。
    fallback = KnowledgeUnitExtractor._normalize_llm_entities([], chapter)
    assert fallback
    assert all(entity["confidence_score"] is None for entity in fallback)
    # Normalizer dedup 路径同样保持 None。
    normalized = KnowledgeUnitNormalizer(entity_normalizer=_EmptyEntityNormalizer())._normalize_entities(
        {"statement": "宁德时代偏强", "entities": [{"entity_name": "宁德时代", "entity_type": "SECURITY"}]},
        {"title": ""},
    )
    assert normalized[0]["confidence_score"] is None


def test_chapter_inferred_entity_confidence_remains_none():
    """§16/§17：SchemaValidator 从 chapter 推导实体时 confidence 保持 None，不再是 0.65。"""
    result = KnowledgeUnitSchemaValidator().validate_one(
        {
            "primary_domain": "MARKET",
            "knowledge_kind": "STATE",
            "expression_type": "AUTHOR_EXPLICIT",
            "predicate_key": "state",
            "statement": "券商偏强",
            "canonical_statement": "券商偏强",
            "evidence": [{"evidence_text": "券商偏强"}],
        },
        chapter={"primary_domain": "MARKET", "entities": ["券商"]},
    )

    assert result["accepted"] is True
    assert "copied_chapter_entities" in result["repairs"]
    assert result["unit"]["entities"]
    assert all(entity["confidence_score"] is None for entity in result["unit"]["entities"])


def test_zero_entity_confidence_remains_zero():
    """§16/§17：0.0 是合法测量值，必须原样保留，不得被 `or` 吞掉变成默认值或 None。"""
    entities = KnowledgeUnitExtractor._normalize_llm_entities(
        [{"entity_name": "宁德时代", "entity_type": "SECURITY", "confidence_score": 0.0}],
        _chapter("券商板块偏强。"),
    )
    assert entities[0]["confidence_score"] == 0.0

    normalized = KnowledgeUnitNormalizer(entity_normalizer=_EmptyEntityNormalizer())._normalize_entities(
        {
            "statement": "宁德时代偏强",
            "entities": [{"entity_name": "宁德时代", "entity_type": "SECURITY", "confidence_score": 0.0}],
        },
        {"title": ""},
    )
    assert normalized[0]["confidence_score"] == 0.0

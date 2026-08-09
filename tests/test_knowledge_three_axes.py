"""P0-3/P0-5/P0-6/P0-7/P0-15/P1-2 三轴状态与验证链路回归测试（§85）。"""

from __future__ import annotations

import pytest

from engines.content.cross_modal_evidence_verifier import CrossModalEvidenceVerifier
from engines.content.external_fact_verifier import ExternalFactVerifier
from engines.content.external_verification.factory import CompositeProvider, build_default_provider
from engines.content.external_verification.market_data_provider import MarketDataVerificationProvider
from engines.content.knowledge_unit_extractor import KnowledgeUnitExtractor
from engines.content.knowledge_unit_normalizer import KnowledgeUnitNormalizer


def _unit(**overrides) -> dict:
    unit = {
        "chapter_index": 0,
        "primary_domain": "MARKET",
        "knowledge_kind": "STATE",
        "statement": "券商板块情绪回暖。",
        "subject_key": "券商",
        "subject_name": "券商",
        "entities": [{"entity_type": "THEME", "entity_key": "券商", "entity_name": "券商", "relation_role": "SUBJECT"}],
        "evidence": [
            {
                "source_type": "ASR",
                "raw_text": "券商板块情绪回暖。",
                "evidence_text": "券商板块情绪回暖。",
                "start_ms": 0,
                "end_ms": 1000,
                "confidence_score": 0.9,
                "is_primary": True,
            }
        ],
    }
    unit.update(overrides)
    return unit


# ---------- P0-3：EvidenceQualityStatus ----------

def test_unknown_evidence_quality_not_promoted():
    """confidence 未测量（None）时，即使 verifier 判 SOURCE_SUPPORTED 也只能到 SOURCE_LOCATED。"""
    evidence = [dict(_unit()["evidence"][0], confidence_score=None)]
    result = KnowledgeUnitNormalizer().normalize([_unit(evidence=evidence)], {"bvid": "BVTEST"})[0]
    assert result["evidence_quality_status"] == "UNKNOWN"
    assert result["support_status"] == "SOURCE_LOCATED"
    assert result["verification_status"] == "SOURCE_LOCATED"
    assert "EVIDENCE_QUALITY_UNKNOWN_CAP" in result["attributes"]["verification"]["reason_codes"]


def test_low_evidence_quality_with_time_window_marks_needs_review():
    evidence = [dict(_unit()["evidence"][0], confidence_score=0.2)]
    result = KnowledgeUnitNormalizer().normalize([_unit(evidence=evidence)], {"bvid": "BVTEST"})[0]
    assert result["evidence_quality_status"] == "LOW"
    assert result["support_status"] == "NEEDS_REVIEW"
    assert result["verification_status"] == "NEEDS_REVIEW"


def test_unsupported_flows_through_without_needs_review_rewrite():
    """verifier 判 UNSUPPORTED（无时间窗）时原样流出，不被质量门改写为 NEEDS_REVIEW。"""
    evidence = [dict(_unit()["evidence"][0], start_ms=None, end_ms=None)]
    result = KnowledgeUnitNormalizer().normalize([_unit(evidence=evidence)], {"bvid": "BVTEST"})[0]
    assert result["support_status"] == "UNSUPPORTED"
    assert result["verification_status"] == "UNSUPPORTED"


def test_measured_high_quality_allows_source_supported():
    result = KnowledgeUnitNormalizer().normalize([_unit()], {"bvid": "BVTEST"})[0]
    assert result["evidence_quality_status"] == "HIGH"
    assert result["support_status"] in {"SOURCE_SUPPORTED", "SOURCE_LOCATED", "NEEDS_REVIEW"}
    assert result["truth_status"] == "NOT_CHECKED"
    assert result["review_status"] == "UNREVIEWED"
    assert result["support_score"] is not None
    assert 0.0 <= result["support_score"] <= 1.0


def test_legacy_truth_status_is_mapped_to_not_checked():
    result = KnowledgeUnitNormalizer().normalize([_unit(truth_status="NOT_EXTERNALLY_VERIFIED")], {"bvid": "BVTEST"})[0]
    assert result["truth_status"] == "NOT_CHECKED"


# ---------- P0-5：Verifier 使用规范化后的实体/主体 ----------

class _RecordingVerifier:
    def __init__(self) -> None:
        self.seen: list[dict] = []

    def verify(self, unit: dict) -> dict:
        self.seen.append(dict(unit))
        return {"support_status": "SOURCE_LOCATED", "support_score": 0.5, "support_probability": 0.5, "reason_codes": [], "checks": {}}


class _StubEntityNormalizer:
    def extract_entities(self, text: str, summary: str, title: str) -> list[dict]:
        return [{"entity_type": "SECURITY", "ticker": "600030", "name": "中信证券", "confidence_score": 0.9}]


def test_verifier_uses_normalized_subject():
    """LLM 未给 subject，normalizer 识别出实体后 verifier 必须看到规范化主体。"""
    recorder = _RecordingVerifier()
    normalizer = KnowledgeUnitNormalizer(entity_normalizer=_StubEntityNormalizer(), verifier=recorder)
    unit = _unit(subject_key=None, subject_name=None, entities=[])
    result = normalizer.normalize([unit], {"bvid": "BVTEST"})[0]
    assert recorder.seen, "verifier 未被执行"
    seen = recorder.seen[0]
    assert seen["subject_key"] == "600030"
    assert seen["subject_name"] == "中信证券"
    assert any(entity.get("ticker") == "600030" for entity in seen["entities"])
    assert result["subject_key"] == "600030"


# ---------- P0-6：ExternalFactVerifier 三轴隔离 ----------

@pytest.fixture
def enabled_env(monkeypatch):
    monkeypatch.setenv("VIDEO_EXTERNAL_FACT_VERIFICATION", "1")


def _fact_unit() -> dict:
    return {
        "knowledge_kind": "FACT",
        "support_status": "SOURCE_SUPPORTED",
        "verification_status": "SOURCE_SUPPORTED",
        "truth_status": "NOT_CHECKED",
    }


def test_external_match_does_not_change_source_support(enabled_env):
    verifier = ExternalFactVerifier(provider=lambda unit: {"status": "MATCH", "observed_value": 21.5})
    result = verifier.verify_many([_fact_unit()])[0]
    assert result["truth_status"] == "EXTERNALLY_VERIFIED"
    assert result["external_verification_status"] == "EXTERNAL_MATCH"
    # 禁止改写 Axis 1（§23）
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert result["verification_status"] == "SOURCE_SUPPORTED"
    assert result["attributes"]["external_verification"]["status"] == "MATCH"


def test_external_conflict_sets_truth_conflict(enabled_env):
    verifier = ExternalFactVerifier(provider=lambda unit: {"status": "CONFLICT"})
    result = verifier.verify_many([_fact_unit()])[0]
    assert result["truth_status"] == "EXTERNAL_CONFLICT"
    assert result["external_verification_status"] == "EXTERNAL_CONFLICT"
    assert result["verification_status"] == "NEEDS_REVIEW"  # 兼容标记
    assert result["support_status"] == "SOURCE_SUPPORTED"  # Axis 1 不动


def test_external_not_found_sets_truth_not_found(enabled_env):
    verifier = ExternalFactVerifier(provider=lambda unit: {"status": "NOT_FOUND"})
    result = verifier.verify_many([_fact_unit()])[0]
    assert result["truth_status"] == "NOT_FOUND"
    assert result["external_verification_status"] == "EXTERNAL_NOT_FOUND"
    assert result["support_status"] == "SOURCE_SUPPORTED"


def test_external_not_run_does_not_promote_fact(monkeypatch):
    monkeypatch.delenv("VIDEO_EXTERNAL_FACT_VERIFICATION", raising=False)
    verifier = ExternalFactVerifier(provider=lambda unit: {"status": "MATCH"})
    result = verifier.verify_many([_fact_unit()])[0]
    assert result["external_verification_status"] == "NOT_RUN"
    assert result["truth_status"] == "NOT_CHECKED"
    assert result["support_status"] == "SOURCE_SUPPORTED"


def test_external_verifier_accepts_provider_object(enabled_env):
    class _Provider:
        def verify(self, unit: dict) -> dict:
            return {"status": "MATCH", "provider": "fake", "source_type": "MARKET_DATA"}

    result = ExternalFactVerifier(provider=_Provider()).verify_many([_fact_unit()])[0]
    assert result["truth_status"] == "EXTERNALLY_VERIFIED"
    assert result["attributes"]["external_verification"]["provider"] == "fake"


# ---------- P0-7：Provider 架构与路由 ----------

class _FakeMarketClient:
    def __init__(self, records) -> None:
        self._records = records

    def get_kline(self, symbol: str, start_date=None, end_date=None, **kwargs):
        return {"records": self._records}


def _price_unit() -> dict:
    return {
        "knowledge_kind": "PRICE_LEVEL",
        "predicate_key": "price_level",
        "statement": "中信证券现价21.5元附近。",
        "as_of_time": "2026-08-02",
        "entities": [{"entity_type": "SECURITY", "entity_key": "600030", "entity_name": "中信证券", "ticker": "600030"}],
    }


def test_market_data_provider_match_with_fake_client():
    provider = MarketDataVerificationProvider(
        market_client=_FakeMarketClient([{"close": 21.4, "date": "2026-08-01"}])
    )
    unit = _price_unit()
    assert provider.supports(unit) is True
    result = provider.verify(unit)
    assert result["status"] == "MATCH"
    assert result["observed_value"] == 21.4
    assert result["source_id"] == "600030"
    assert result["as_of"] == "2026-08-01"


def test_market_data_provider_conflict_with_fake_client():
    provider = MarketDataVerificationProvider(
        market_client=_FakeMarketClient([{"close": 30.0, "date": "2026-08-01"}])
    )
    assert provider.verify(_price_unit())["status"] == "CONFLICT"


def test_market_data_provider_not_found_when_no_data():
    provider = MarketDataVerificationProvider(market_client=_FakeMarketClient([]))
    assert provider.verify(_price_unit())["status"] == "NOT_FOUND"


def test_market_data_provider_error_when_client_fails():
    class _BrokenClient:
        def get_kline(self, symbol: str, **kwargs):
            raise RuntimeError("bridge down")

    provider = MarketDataVerificationProvider(market_client=_BrokenClient())
    assert provider.verify(_price_unit())["status"] == "ERROR"


def test_factory_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VIDEO_EXTERNAL_FACT_VERIFICATION", raising=False)
    assert build_default_provider() is None


def test_factory_composite_routing(monkeypatch):
    monkeypatch.setenv("VIDEO_EXTERNAL_FACT_VERIFICATION", "1")
    composite = build_default_provider()
    assert isinstance(composite, CompositeProvider)
    # POLICY_FACT → policy provider（占位 NOT_FOUND）
    policy_result = composite.verify({"knowledge_kind": "POLICY_FACT", "subject_key": "货币政策"})
    assert policy_result["status"] == "NOT_FOUND"
    assert policy_result["source_type"] == "POLICY_SOURCE"
    # 无路由命中 → composite NOT_FOUND
    unrouted = composite.verify({"knowledge_kind": "STATE", "statement": "市场情绪回暖"})
    assert unrouted["status"] == "NOT_FOUND"
    assert unrouted.get("reason") == "NO_PROVIDER_SUPPORTS"


# ---------- P0-15：CrossModal Verifier ----------

def _cross_modal_unit(**overrides) -> dict:
    unit = _unit(
        statement="亿纬锂能净利润增长120%。",
        subject_key="300014",
        subject_name="亿纬锂能",
        support_status="SOURCE_SUPPORTED",
        support_score=0.9,
        entities=[{"entity_type": "SECURITY", "entity_key": "300014", "entity_name": "亿纬锂能", "ticker": "300014"}],
    )
    unit.update(overrides)
    return unit


def _ocr_evidence(text: str, score: float) -> dict:
    return {
        "source_type": "OCR",
        "source_ref": "window_0",
        "raw_text": text,
        "start_ms": 0,
        "end_ms": 1000,
        "confidence_score": score,
        "is_primary": False,
    }


def test_cross_modal_upgrade_on_double_match():
    unit = _cross_modal_unit()
    unit["evidence"] = unit["evidence"] + [_ocr_evidence("亿纬锂能 净利润同比 +120%", 0.97)]
    result = CrossModalEvidenceVerifier().verify_many([unit])[0]
    assert result["support_status"] == "CROSS_MODAL_SUPPORTED"
    cross = result["attributes"]["cross_modal_verification"]
    assert cross["status"] == "CROSS_MODAL_SUPPORTED"
    assert cross["asr_support_score"] == 0.9
    assert cross["ocr_support_score"] == 0.97
    assert cross["matched_blocks"]


def test_cross_modal_not_upgraded_when_ocr_score_low():
    unit = _cross_modal_unit()
    unit["evidence"] = unit["evidence"] + [_ocr_evidence("亿纬锂能 净利润同比 +120%", 0.80)]
    result = CrossModalEvidenceVerifier().verify_many([unit])[0]
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert "cross_modal_verification" not in (result.get("attributes") or {})


def test_cross_modal_conflict_downgrades_to_needs_review():
    unit = _cross_modal_unit()
    unit["evidence"] = unit["evidence"] + [_ocr_evidence("亿纬锂能 净利润同比 +30%", 0.97)]
    result = CrossModalEvidenceVerifier().verify_many([unit])[0]
    assert result["support_status"] == "NEEDS_REVIEW"
    cross = result["attributes"]["cross_modal_verification"]
    assert cross["status"] == "CROSS_MODAL_CONFLICT"
    assert "CROSS_MODAL_CONFLICT" in cross["reason_codes"]


def test_cross_modal_no_ocr_evidence_untouched():
    unit = _cross_modal_unit()
    result = CrossModalEvidenceVerifier().verify_many([unit])[0]
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert "cross_modal_verification" not in (result.get("attributes") or {})


def test_cross_modal_accepts_external_ocr_list():
    unit = _cross_modal_unit()
    ocr = [{"source_type": "OCR", "text": "亿纬锂能 净利润同比 +120%", "score": 0.96, "start_ms": 0, "end_ms": 1000}]
    result = CrossModalEvidenceVerifier().verify_many([unit], ocr_evidence=ocr)[0]
    assert result["support_status"] == "CROSS_MODAL_SUPPORTED"


# ---------- §12：Cross-Modal 结构化数字 Gate ----------

def test_cross_modal_preserves_unit():
    """20% vs 20倍：单位不同既不得判一致也不得判冲突（§12.4）。"""
    unit = _cross_modal_unit(statement="亿纬锂能净利润20%。")
    unit["evidence"] = unit["evidence"] + [_ocr_evidence("亿纬锂能 净利润20倍", 0.97)]
    result = CrossModalEvidenceVerifier().verify_many([unit])[0]
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert "cross_modal_verification" not in (result.get("attributes") or {})


def test_cross_modal_rejects_metric_mismatch():
    """净利润增长20% vs 营收增长20%：metric 冲突，不得升级也不得判冲突。"""
    unit = _cross_modal_unit(statement="亿纬锂能净利润增长20%。")
    unit["evidence"] = unit["evidence"] + [_ocr_evidence("亿纬锂能 营收增长20%", 0.97)]
    result = CrossModalEvidenceVerifier().verify_many([unit])[0]
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert "cross_modal_verification" not in (result.get("attributes") or {})


def test_cross_modal_subject_only_does_not_promote():
    """无数字 claim 仅凭主体命中不得升级 CROSS_MODAL_SUPPORTED（§11/§12.3）。"""
    unit = _cross_modal_unit(statement="亿纬锂能盈利能力明显改善。")
    unit["evidence"] = unit["evidence"] + [_ocr_evidence("亿纬锂能", 0.97)]
    result = CrossModalEvidenceVerifier().verify_many([unit])[0]
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert "cross_modal_verification" not in (result.get("attributes") or {})


def test_cross_modal_direction_mismatch_does_not_promote():
    """claim 含方向词（增长）时 OCR 方向不一致（下降）不得升级（§12.3）。"""
    unit = _cross_modal_unit(statement="亿纬锂能净利润增长20%。")
    unit["evidence"] = unit["evidence"] + [_ocr_evidence("亿纬锂能 净利润下降20%", 0.97)]
    result = CrossModalEvidenceVerifier().verify_many([unit])[0]
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert "cross_modal_verification" not in (result.get("attributes") or {})


# ---------- P1-2：EntityResolutionTrace ----------

def test_entity_corrections_unverified_caps_quality_and_records_reason():
    unit = _unit(
        evidence=[dict(_unit()["evidence"][0], confidence_score=0.9)],
        entity_corrections=[
            {
                "raw_expression": "义伟",
                "canonical_name": "亿纬锂能",
                "ticker": "300014",
                "resolution_method": ["phonetic_similarity"],
                "confidence": 0.9,
            }
        ],
        entities=[{"entity_type": "SECURITY", "entity_key": "300014", "entity_name": "亿纬锂能", "ticker": "300014", "relation_role": "SUBJECT"}],
    )
    result = KnowledgeUnitNormalizer().normalize([unit], {"bvid": "BVTEST"})[0]
    resolution = result["attributes"]["entity_resolution"]
    assert resolution["status"] == "UNVERIFIED"
    assert resolution["items"][0]["raw_expression"] == "义伟"
    # 高风险实体仅 LLM 自述：evidence quality 不得高于 MEDIUM
    assert result["evidence_quality_status"] == "MEDIUM"
    assert "ENTITY_RESOLUTION_UNVERIFIED" in result["attributes"]["verification"]["reason_codes"]


def test_entity_corrections_with_dictionary_evidence_resolved():
    unit = _unit(
        entity_corrections=[
            {
                "raw_expression": "义伟",
                "canonical_name": "亿纬锂能",
                "ticker": "300014",
                "resolution_method": ["entity_dictionary", "phonetic_similarity"],
                "confidence": 0.96,
            }
        ],
        entities=[{"entity_type": "SECURITY", "entity_key": "300014", "entity_name": "亿纬锂能", "ticker": "300014", "relation_role": "SUBJECT"}],
    )
    result = KnowledgeUnitNormalizer().normalize([unit], {"bvid": "BVTEST"})[0]
    resolution = result["attributes"]["entity_resolution"]
    assert resolution["status"] == "RESOLVED"
    assert result["evidence_quality_status"] == "HIGH"


def test_extractor_passes_through_entity_corrections():
    extractor = KnowledgeUnitExtractor(model_client=type("Stub", (), {"available": lambda self: False})())
    chapter = {"chapter_index": 0, "primary_domain": "MARKET", "windows": []}
    unit = extractor._normalize_llm_unit(
        {
            "knowledge_kind": "STATE",
            "conclusion": "亿纬锂能排产改善。",
            "entity_corrections": [
                {"raw_expression": "义伟", "canonical_name": "亿纬锂能", "ticker": "300014", "resolution_method": ["nearby_ocr"], "confidence": 0.9},
                "garbage",
            ],
        },
        chapter,
        {"provider": "stub", "model": "stub"},
    )
    assert unit["entity_corrections"] == [
        {"raw_expression": "义伟", "canonical_name": "亿纬锂能", "ticker": "300014", "resolution_method": ["nearby_ocr"], "confidence": 0.9}
    ]

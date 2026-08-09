"""ClaimEvidenceVerifier V2 回归测试（设计文档 §85 Semantic Verification）。"""

from engines.content.claim_evidence_verifier import ClaimEvidenceVerifier


def _unit(statement, raw_text, **overrides):
    unit = {
        "statement": statement,
        "evidence": [{"is_primary": True, "start_ms": 0, "end_ms": 1000, "raw_text": raw_text}],
    }
    unit.update(overrides)
    return unit


def test_high_quality_matching_evidence_is_source_supported():
    result = ClaimEvidenceVerifier().verify(_unit("黄金价格今年上涨20%。", "黄金价格今年上涨20%。", subject_name="黄金"))
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert result["support_score"] <= 1.0


def test_missing_evidence_is_unsupported():
    result = ClaimEvidenceVerifier().verify({"statement": "任意陈述", "evidence": []})
    assert result["support_status"] == "UNSUPPORTED"
    assert result["reason_codes"] == ["EVIDENCE_NOT_LOCATED"]
    assert result["support_score"] == 0.0


def test_affirmative_claim_rejected_against_negated_source():
    result = ClaimEvidenceVerifier().verify(_unit("公司利润增长。", "公司利润没有增长。"))
    assert result["support_status"] != "SOURCE_SUPPORTED"
    assert "NEGATION_MATCH_FAILED" in result["reason_codes"]


def test_negated_claim_rejected_against_affirmative_source():
    result = ClaimEvidenceVerifier().verify(_unit("公司利润没有增长。", "公司利润增长。"))
    assert result["support_status"] != "SOURCE_SUPPORTED"
    assert "NEGATION_MATCH_FAILED" in result["reason_codes"]


def test_entity_metric_number_cross_binding_rejected():
    result = ClaimEvidenceVerifier().verify(
        _unit("B公司利润增长20%。", "A公司利润增长20%，B公司营收增长30%。", subject_name="B公司")
    )
    assert result["support_status"] != "SOURCE_SUPPORTED"
    assert "NUMBER_MATCH_FAILED" in result["reason_codes"]


def test_missing_condition_rejected():
    result = ClaimEvidenceVerifier().verify(_unit("建议减仓。", "如果跌破30元，我才会考虑减仓。"))
    assert result["support_status"] != "SOURCE_SUPPORTED"
    assert "CONDITION_DROPPED" in result["reason_codes"]
    assert result["checks"]["condition_dropped"] is True


def test_explicit_condition_is_honored():
    result = ClaimEvidenceVerifier().verify(
        _unit("如果跌破30元则考虑减仓。", "如果跌破30元，我才会考虑减仓。", condition_text="如果跌破30元")
    )
    assert result["checks"]["condition_match"] is True
    assert result["support_status"] == "SOURCE_SUPPORTED"


def test_wrong_unit_rejected():
    result = ClaimEvidenceVerifier().verify(_unit("公司估值上涨20%。", "公司估值上涨20倍。"))
    assert result["support_status"] != "SOURCE_SUPPORTED"
    assert "UNIT_MATCH_FAILED" in result["reason_codes"]


def test_wrong_time_scope_rejected():
    result = ClaimEvidenceVerifier().verify(_unit("公司今年利润增长20%。", "公司去年利润增长20%。"))
    assert result["support_status"] != "SOURCE_SUPPORTED"
    assert "TIME_MATCH_FAILED" in result["reason_codes"]


def test_support_score_never_exceeds_one():
    # 全 7 项硬 check 通过 + bigram 重叠 1.0，旧公式会给 1.05+，V2 必须 clamp。
    result = ClaimEvidenceVerifier().verify(_unit("黄金价格今年上涨20%。", "黄金价格今年上涨20%。", subject_name="黄金"))
    assert all(result["checks"][key] for key in ("number_match", "entity_match", "direction_match", "negation_match", "condition_match", "unit_match", "time_match"))
    assert result["support_score"] <= 1.0
    assert result["support_probability"] <= 1.0


def test_chinese_percent_claim_matches_arabic_source():
    result = ClaimEvidenceVerifier().verify(_unit("公司净利润增长百分之一百五。", "公司净利润同比增长150%。"))
    assert result["checks"]["number_match"] is True
    assert result["support_status"] == "SOURCE_SUPPORTED"


def test_approximate_range_matches_point_inside():
    result = ClaimEvidenceVerifier().verify(_unit("公司PE十来倍。", "公司当前PE为15倍。"))
    assert result["checks"]["number_match"] is True


def test_approximate_range_rejects_point_outside():
    result = ClaimEvidenceVerifier().verify(_unit("公司PE十来倍。", "公司当前PE为25倍。"))
    assert result["checks"]["number_match"] is False
    assert result["support_status"] != "SOURCE_SUPPORTED"


def test_judge_cannot_override_stage_a_hard_failure():
    judge = lambda ctx: {"label": "SUPPORTED", "score": 1.0, "reason_codes": []}
    result = ClaimEvidenceVerifier(judge=judge).verify(_unit("公司利润增长。", "公司利润没有增长。"))
    assert result["support_status"] != "SOURCE_SUPPORTED"


def test_judge_contradicted_downgrades_supported():
    judge = lambda ctx: {"label": "CONTRADICTED", "score": 0.9, "reason_codes": ["NLI_CONTRADICTION"]}
    result = ClaimEvidenceVerifier(judge=judge).verify(_unit("黄金价格今年上涨20%。", "黄金价格今年上涨20%。", subject_name="黄金"))
    assert result["support_status"] == "NEEDS_REVIEW"
    assert "JUDGE_CONTRADICTED" in result["reason_codes"]
    assert result["checks"]["judge"]["label"] == "CONTRADICTED"

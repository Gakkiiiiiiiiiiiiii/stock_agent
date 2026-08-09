"""P0-13/P0-14 摘要质量门回归测试（设计文档 §85 Summary 组）。

全部使用注入的 fake model client，不依赖真实 LLM。
"""

from __future__ import annotations

from engines.content.analysis_document_policy import (
    MARK_AUTHOR_CLAIM,
    MARK_VERIFIED_FACT,
    AnalysisDocumentPolicy,
)
from engines.content.video_analysis_document_generator import VideoAnalysisDocumentGenerator


class CaptureModel:
    """记录 prompt 的 LLM 桩，返回一份合法的 curated JSON。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def available(self):
        return True

    def complete(self, **kwargs):
        self.prompts.append(str(kwargs.get("prompt") or ""))
        return {
            "provider": "fake",
            "model": "fake-k3",
            "content": (
                '{"core_summary":"市场处于温和去杠杆阶段，关注风险边界。",'
                '"key_points":["融资余额持续回落。"],'
                '"chapter_summaries":[{"chapter_index":0,"title":"市场状态与风险","summary":"市场去杠杆，控制仓位。"}]}'
            ),
        }


def _unit(
    statement: str,
    *,
    kind: str = "STATE",
    support: str = "SOURCE_SUPPORTED",
    truth: str = "NOT_CHECKED",
    review: str = "UNREVIEWED",
    quality: str = "HIGH",
    extraction_confidence: float = 0.9,
    with_evidence: bool = True,
) -> dict:
    return {
        "knowledge_kind": kind,
        "statement": statement,
        "chapter_index": 0,
        "support_status": support,
        "truth_status": truth,
        "review_status": review,
        "evidence_quality_status": quality,
        "extraction_confidence": extraction_confidence,
        "evidence": [{"source_type": "ASR", "raw_text": statement}] if with_evidence else [],
    }


def _chapters() -> list[dict]:
    return [{"chapter_index": 0, "title": "市场状态", "primary_domain": "MARKET", "start_ms": 0, "end_ms": 60000}]


def _metadata() -> dict:
    return {"title": "市场复盘", "publish_time": "20260729"}


def test_analysis_document_excludes_needs_review_units():
    model = CaptureModel()
    generator = VideoAnalysisDocumentGenerator(model_client=model)
    units = [
        _unit("融资余额持续回落", support="SOURCE_SUPPORTED"),
        _unit("低质量证据的论断", support="NEEDS_REVIEW", quality="LOW"),
        _unit("完全没有证据的论断", support="UNSUPPORTED", quality="UNKNOWN", with_evidence=False),
        _unit("仅定位到来源的论断", support="SOURCE_LOCATED"),
        _unit("被人工驳回的论断", support="SOURCE_SUPPORTED", review="REJECTED"),
    ]

    result = generator.generate(metadata=_metadata(), chapters=_chapters(), units=units)

    assert model.prompts, "有通过门禁的 unit 时应调用 LLM"
    prompt = model.prompts[0]
    assert "融资余额持续回落" in prompt
    for excluded in ("低质量证据的论断", "完全没有证据的论断", "仅定位到来源的论断", "被人工驳回的论断"):
        assert excluded not in prompt
    quality = result["quality_summary"]
    assert quality["summary_source_unit_count"] == 1
    assert quality["excluded_low_quality_count"] == 4
    assert quality["needs_review_count"] == 1
    assert quality["unsupported_count"] == 1


def test_unverified_fact_is_rendered_as_author_claim():
    model = CaptureModel()
    generator = VideoAnalysisDocumentGenerator(model_client=model)
    units = [
        _unit("该公司PE为15倍", kind="FACT", support="SOURCE_SUPPORTED", truth="NOT_CHECKED"),
    ]

    generator.generate(metadata=_metadata(), chapters=_chapters(), units=units)

    prompt = model.prompts[0]
    assert f"{MARK_AUTHOR_CLAIM} FACT: 该公司PE为15倍" in prompt
    # prompt 固定包含标记说明文本，因此只对 outline 行断言未带 [已验证]。
    assert f"{MARK_VERIFIED_FACT} FACT" not in prompt
    # prompt 必须向 LLM 说明标记的书写规则（归因形式、禁止客观陈述）。
    assert "视频作者称" in prompt
    assert "客观事实" in prompt


def test_objective_fact_requires_external_verification():
    model = CaptureModel()
    generator = VideoAnalysisDocumentGenerator(model_client=model)
    units = [
        _unit("该公司PE为15倍", kind="FACT", support="CROSS_MODAL_SUPPORTED", truth="EXTERNALLY_VERIFIED"),
        _unit("另一家PB为2倍", kind="VALUATION", support="SOURCE_SUPPORTED", truth="NOT_FOUND"),
    ]

    generator.generate(metadata=_metadata(), chapters=_chapters(), units=units)

    prompt = model.prompts[0]
    assert f"{MARK_VERIFIED_FACT} FACT: 该公司PE为15倍" in prompt
    assert f"{MARK_AUTHOR_CLAIM} VALUATION: 另一家PB为2倍" in prompt


def test_quality_summary_replaces_fake_confidence():
    model = CaptureModel()
    generator = VideoAnalysisDocumentGenerator(model_client=model)
    units = [
        _unit("已验证事实", kind="FACT", support="SOURCE_SUPPORTED", truth="EXTERNALLY_VERIFIED", quality="HIGH"),
        _unit("未验证事实", kind="FACT", support="SOURCE_SUPPORTED", truth="NOT_CHECKED", quality="MEDIUM"),
        _unit("低质量观点", kind="STATE", support="NEEDS_REVIEW", quality="LOW"),
        _unit("无证据事实", kind="FACT", support="UNSUPPORTED", quality="UNKNOWN", with_evidence=False),
        _unit("跨模态风险", kind="RISK_CONDITION", support="CROSS_MODAL_SUPPORTED", quality="HIGH"),
        _unit("普通观点", kind="STATE", support="SOURCE_SUPPORTED", quality="UNKNOWN"),
    ]

    result = generator.generate(metadata=_metadata(), chapters=_chapters(), units=units)

    quality = result["quality_summary"]
    assert quality["evidence_coverage"] == round(5 / 6, 4)
    assert quality["measured_evidence_ratio"] == round(4 / 6, 4)
    assert quality["source_supported_ratio"] == round(4 / 6, 4)
    assert quality["cross_modal_supported_ratio"] == round(1 / 6, 4)
    assert quality["externally_verified_fact_ratio"] == round(1 / 3, 4)
    assert quality["needs_review_count"] == 1
    assert quality["unsupported_count"] == 1
    assert quality["unknown_evidence_quality_count"] == 2
    assert quality["summary_source_unit_count"] == 4
    assert quality["excluded_low_quality_count"] == 2
    # P0-14：confidence_score 不再是 extraction_confidence 平均值（0.9）。
    assert result["confidence_score"] is None
    assert result["confidence_score"] != 0.9


def test_summary_input_is_classified_by_policy():
    units = [
        _unit("已验证事实", kind="FACT", truth="EXTERNALLY_VERIFIED"),
        _unit("作者观点", kind="CAUSAL_THESIS"),
        _unit("下周预测", kind="FORECAST"),
        _unit("风险条件", kind="RISK_CONDITION"),
        _unit("未验证事实", kind="FACT", truth="NOT_CHECKED"),
        _unit("被排除", support="UNSUPPORTED"),
    ]

    classified = AnalysisDocumentPolicy.classify(units)

    assert [u["statement"] for u in classified["verified_facts"]] == ["已验证事实"]
    assert [u["statement"] for u in classified["attributed_opinions"]] == ["作者观点"]
    assert [u["statement"] for u in classified["forecasts"]] == ["下周预测"]
    assert [u["statement"] for u in classified["risks"]] == ["风险条件"]
    assert [u["statement"] for u in classified["others"]] == ["未验证事实"]
    assert classified["excluded_low_quality_count"] == 1


def test_all_units_excluded_still_produces_document_without_llm():
    model = CaptureModel()
    generator = VideoAnalysisDocumentGenerator(model_client=model)
    units = [
        _unit("低质量论断", support="NEEDS_REVIEW", quality="LOW"),
        _unit("无证据论断", support="UNSUPPORTED", quality="UNKNOWN", with_evidence=False),
    ]

    result = generator.generate(metadata=_metadata(), chapters=_chapters(), units=units)

    assert model.prompts == [], "全部 unit 被排除时不应调用 LLM"
    assert "质量门槛" in result["core_summary"]
    assert result["key_points"] == []
    assert len(result["chapter_summaries"]) == 1
    assert result["quality_summary"]["summary_source_unit_count"] == 0
    assert result["quality_summary"]["excluded_low_quality_count"] == 2
    assert result["document_markdown"]

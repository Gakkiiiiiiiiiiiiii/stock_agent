from __future__ import annotations

import pytest

from app.model_providers import AnalysisModelClient, AnalysisModelSettings
from engines.content.video_analysis_document_generator import VideoAnalysisDocumentGenerator


class CuratedSummaryModel:
    def available(self):
        return True

    def complete(self, **kwargs):
        _ = kwargs
        return {
            "provider": "openai_compatible",
            "model": "k3",
            "content": """{
              "core_summary": "市场正处于温和去杠杆阶段，权重科技承压，微盘相对受益于低杠杆与低拥挤度。",
              "key_points": ["融资余额持续回落，反映市场在温和去杠杆。", "高杠杆拥挤方向调整时应控制仓位。", "微盘因杠杆和拥挤度较低，短期相对占优。"],
              "chapter_summaries": [{"chapter_index": 0, "title": "市场去杠杆与风格切换", "summary": "市场去杠杆下，权重科技承压，微盘相对占优。"}]
            }""",
        }


def test_generator_uses_curated_llm_summary_and_chapter_brief():
    generator = VideoAnalysisDocumentGenerator(model_client=CuratedSummaryModel())
    result = generator.generate(
        metadata={"title": "市场复盘", "publish_time": "20260729"},
        chapters=[{"chapter_index": 0, "title": "市场状态", "primary_domain": "MARKET"}],
        units=[
            {"knowledge_kind": "STATE", "statement": "融资余额持续回落", "extraction_confidence": 0.9},
            {"knowledge_kind": "RISK_CONDITION", "statement": "高杠杆方向调整应控制仓位", "extraction_confidence": 0.9},
        ],
    )

    assert result["generator_model"] == "k3"
    assert len(result["key_points"]) == 3
    assert result["chapter_summaries"][0]["summary"].startswith("市场去杠杆")
    assert result["chapter_summaries"][0]["title"] == "市场去杠杆与风格切换"
    assert "## 重点结论" in result["document_markdown"]


class _CaptureResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "{}"}}]}


class _CaptureHttpClient:
    def __init__(self):
        self.payload = None

    def post(self, url, headers, json):
        _ = url, headers
        self.payload = json
        return _CaptureResponse()


def test_kimi_k3_forces_temperature_one():
    http_client = _CaptureHttpClient()
    client = AnalysisModelClient(
        settings=AnalysisModelSettings(provider="openai_compatible", model="k3", base_url="https://example.test", api_key="key"),
        http_client=http_client,
    )

    client.complete(prompt="test", temperature=0.1, response_format={"type": "json_object"})

    assert http_client.payload["temperature"] == 1.0
    assert http_client.payload["response_format"] == {"type": "json_object"}


class PlainTextBriefModel:
    def available(self):
        return True

    def complete(self, **kwargs):
        _ = kwargs
        return {
            "provider": "openai_compatible",
            "model": "k3",
            "content": "摘要：市场处于出清尾声，等待流动性修复。\n要点：被动抛压减弱。\n章节0：半导体出清接近尾声，量能萎缩。\n章节1：美联储政策路径影响外资流向。",
        }


def test_generator_rejects_non_json_output_instead_of_rule_fallback():
    generator = VideoAnalysisDocumentGenerator(model_client=PlainTextBriefModel())
    with pytest.raises(RuntimeError, match="K3 分析文档生成失败"):
        generator.generate(
            metadata={"title": "复盘", "publish_time": "20260729"},
            chapters=[
                {"chapter_index": 0, "title": "半导体", "primary_domain": "INDUSTRY"},
                {"chapter_index": 1, "title": "美联储", "primary_domain": "MACRO"},
            ],
            units=[{"knowledge_kind": "STATE", "statement": "半导体出清接近尾声", "chapter_index": 0, "extraction_confidence": 0.9}],
        )


def test_generator_rejects_partial_curated_summaries_instead_of_unit_fallback():
    class PartialCuratedModel:
        def available(self):
            return True

        def complete(self, **kwargs):
            _ = kwargs
            return {
                "provider": "fake",
                "model": "k3",
                "content": '{"core_summary":"核心判断","key_points":["要点一"],"chapter_summaries":[{"chapter_index":0,"summary":"第0章精炼摘要"}]}',
            }

    generator = VideoAnalysisDocumentGenerator(model_client=PartialCuratedModel())
    with pytest.raises(RuntimeError, match="K3 分析文档生成失败"):
        generator.generate(
            metadata={"title": "复盘", "publish_time": "20260729"},
            chapters=[
                {"chapter_index": 0, "title": "指数", "primary_domain": "MARKET", "summary": "大家晚上好欢迎收看"},
                {"chapter_index": 1, "title": "半导体", "primary_domain": "INDUSTRY", "summary": "这个呢比之前来的好为什么呢"},
            ],
            units=[{"knowledge_kind": "STATE", "statement": "半导体出清接近尾声，量能萎缩。", "chapter_index": 1}],
        )

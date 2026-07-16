from __future__ import annotations

from engines.content.video_summarizer import VideoSummarizer


class FakeModelClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, system: str | None = None, temperature: float = 0.2) -> dict:
        _ = (system, temperature)
        self.prompts.append(prompt)
        if "片段 1 时间范围" in prompt:
            return {"provider": "fake", "model": "fake-model", "content": "第一段讲市场企稳和反弹。"}
        if "片段 2 时间范围" in prompt:
            return {"provider": "fake", "model": "fake-model", "content": "第二段讲科技分化和节奏控制。"}
        return {
            "provider": "fake",
            "model": "fake-model",
            "content": (
                '{"core_summary":"市场进入反弹观察期。","bull_points":["情绪改善"],'
                '"bear_points":["科技分化"],"themes":["科技"],"symbols":[],'
                '"catalysts":["指数修复"],"risks":["追高风险"],'
                '"actionable_view":"更适合低吸，不宜追高。","evidence_segments":[],"confidence_score":0.76}'
            ),
        }


def test_video_summarizer_uses_chunk_outline_for_long_transcript():
    model_client = FakeModelClient()
    summarizer = VideoSummarizer(model_client=model_client)
    transcript = {
        "text": "",
        "segments": [
            {"start_ms": 0, "end_ms": 1000, "text": "A" * 1800},
            {"start_ms": 1000, "end_ms": 2000, "text": "B" * 1800},
            {"start_ms": 2000, "end_ms": 3000, "text": "C" * 1800},
        ],
    }
    result = summarizer.summarize({"title": "测试视频", "author_name": "测试作者"}, transcript, mode="investment")
    assert result["core_summary"] == "市场进入反弹观察期。"
    assert result["themes"] == ["科技"]
    assert len(model_client.prompts) == 4


def test_build_segment_chunks_covers_full_timeline():
    chunks = VideoSummarizer._build_segment_chunks(
        [
            {"start_ms": 0, "end_ms": 1000, "text": "A" * 1500},
            {"start_ms": 1000, "end_ms": 2000, "text": "B" * 1500},
            {"start_ms": 2000, "end_ms": 3000, "text": "C" * 1500},
            {"start_ms": 3000, "end_ms": 4000, "text": "D" * 1500},
            {"start_ms": 4000, "end_ms": 5000, "text": "E" * 1500},
            {"start_ms": 5000, "end_ms": 6000, "text": "F" * 1500},
            {"start_ms": 6000, "end_ms": 7000, "text": "G" * 1500},
            {"start_ms": 7000, "end_ms": 8000, "text": "H" * 1500},
        ],
        target_chars=2000,
        max_chunks=3,
    )
    assert len(chunks) == 3
    assert chunks[0]["start_ms"] == 0
    assert chunks[-1]["end_ms"] == 8000


def test_video_summarizer_tolerates_non_numeric_confidence():
    class ConfidenceStringModelClient(FakeModelClient):
        def complete(self, prompt: str, system: str | None = None, temperature: float = 0.2) -> dict:
            _ = (prompt, system, temperature)
            return {
                "provider": "fake",
                "model": "fake-model",
                "content": (
                    '{"core_summary":"摘要","bull_points":[],"bear_points":[],"themes":[],"symbols":[],'
                    '"catalysts":[],"risks":[],"actionable_view":"观望","evidence_segments":[],'
                    '"confidence_score":"中高（推演逻辑清晰）"}'
                ),
            }

    summarizer = VideoSummarizer(model_client=ConfidenceStringModelClient())
    result = summarizer.summarize(
        {"title": "测试视频", "author_name": "测试作者"},
        {"text": "测试转写", "segments": []},
        mode="investment",
    )
    assert result["confidence_score"] == 0.5


def test_collect_symbols_prefers_name_and_ticker_labels():
    symbols = VideoSummarizer._collect_symbols_from_events(
        [
            {
                "entities": [
                    {"name": "成都先导", "ticker": "688222", "entity_type": "EQUITY"},
                    {"name": "XAUUSD", "ticker": "XAUUSD", "entity_type": "COMMODITY"},
                ]
            }
        ]
    )

    assert "成都先导 (688222)" in symbols
    assert "XAUUSD" in symbols


def test_prepare_chunk_outline_keeps_late_stock_analysis_chunks():
    chunks = [
        {"chunk_index": index, "start_ms": index * 1000, "topic": f"topic-{index}", "transcript_text": "普通内容", "ocr_text": "", "visual_focus": ""}
        for index in range(10)
    ]
    chunks.append(
        {
            "chunk_index": 10,
            "start_ms": 10000,
            "topic": "688222",
            "transcript_text": "这里是买点，但要注意洗盘风险。",
            "ocr_text": "KR688222成都先导 成都先导(日线.前复权)",
            "visual_focus": "K线结构",
        }
    )

    outline = VideoSummarizer._prepare_chunk_outline(chunks)

    assert "688222" in outline
    assert "成都先导" in outline


def test_apply_symbol_aliases_replaces_bare_ticker_in_summary_text():
    text = "医药板块表现相对强势，但部分个股（如688222）存在技术洗盘需求。"
    updated = VideoSummarizer._apply_symbol_aliases(text, ["成都先导 (688222.SH)"])

    assert "成都先导 (688222.SH)" in updated

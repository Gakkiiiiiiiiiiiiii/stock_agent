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

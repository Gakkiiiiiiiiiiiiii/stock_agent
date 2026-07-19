from __future__ import annotations

from engines.content.financial_event_extractor import FinancialEventExtractor


class FakeModelClient:
    def __init__(self) -> None:
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, system: str | None = None, temperature: float = 0.2) -> dict:
        _ = (prompt, system, temperature)
        self.calls += 1
        return {
            "content": (
                '[{"event_type":"RISK","claim_type":"OPINION","sentiment":"BEARISH",'
                '"statement":"半导体存在补跌风险","condition_text":"","invalidation_text":"",'
                '"time_expression":"","certainty":0.7,"confidence_score":0.8,"attributes":{}}]'
            )
        }


def _make_chunk(chunk_index: int, text: str) -> dict:
    return {
        "chunk_index": chunk_index,
        "start_ms": chunk_index * 1000,
        "end_ms": (chunk_index + 1) * 1000,
        "topic": "半导体",
        "transcript_text": text,
        "ocr_text": "",
        "visual_focus": "",
        "frame_refs": [],
        "confidence_score": 0.7,
    }


def test_financial_event_extractor_falls_back_to_rules_beyond_chunk_budget(monkeypatch):
    monkeypatch.setenv("VIDEO_EVENT_LLM_MAX_CHUNKS", "2")
    model_client = FakeModelClient()
    extractor = FinancialEventExtractor(model_client=model_client)
    metadata = {"title": "测试视频", "description": "", "publish_time": "20260715"}
    chunks = [
        _make_chunk(0, "半导体今天继续回调，存在补跌风险。"),
        _make_chunk(1, "市场正在去杠杆，科技承压。"),
        _make_chunk(2, "投资者需要注意波动。"),
    ]

    video_type, events = extractor.extract(metadata=metadata, chunks=chunks)

    assert video_type
    # 前 2 个分块走 LLM，超出预算的分块回退为规则抽取
    assert model_client.calls == 2
    assert events
    assert any(event["event_type"] == "RISK" for event in events)


def test_financial_event_extractor_uses_llm_within_chunk_budget(monkeypatch):
    monkeypatch.setenv("VIDEO_EVENT_LLM_MAX_CHUNKS", "3")
    model_client = FakeModelClient()
    extractor = FinancialEventExtractor(model_client=model_client)
    metadata = {"title": "测试视频", "description": "", "publish_time": "20260715"}
    chunks = [_make_chunk(0, "半导体今天继续回调，存在补跌风险。")]

    _, events = extractor.extract(metadata=metadata, chunks=chunks)

    assert model_client.calls == 1
    assert events[0]["statement"] == "半导体存在补跌风险"

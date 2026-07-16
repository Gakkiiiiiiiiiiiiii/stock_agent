from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import httpx

from engines.content.video_vision_service import VideoVisionService


class EmptyOcrService:
    def available(self):
        return True

    def extract_text(self, image_path):
        _ = image_path
        return ""


class UnavailableVisualModel:
    def available(self):
        return False


class RichOcrService:
    def available(self):
        return True

    def extract_text(self, image_path):
        _ = image_path
        return "880491 半导体 7337.87 21.03\n880446 电气设备 2048.01 -37.72"


class TextOnlyVisualModel:
    def __init__(self) -> None:
        self.create_calls = 0
        self.complete_calls = 0

    def available(self):
        return True

    def create_chat_completion(self, *args, **kwargs):
        _ = (args, kwargs)
        self.create_calls += 1
        request = httpx.Request("POST", "https://example.com/chat/completions")
        response = httpx.Response(
            400,
            request=request,
            json={
                "error": {
                    "message": "Failed to deserialize the JSON body: unknown variant `image_url`, expected `text`"
                }
            },
        )
        raise httpx.HTTPStatusError("400 Bad Request", request=request, response=response)

    def complete(self, prompt, system=None, temperature=0.2):
        _ = (prompt, system, temperature)
        self.complete_calls += 1
        return {
            "provider": "fake",
            "model": "fake-model",
            "content": (
                '{"visual_summary":"画面大概率是板块行情列表，半导体与电气设备处于跌幅榜前列。",'
                '"themes":["半导体"],"symbols":["880491","880446"],'
                '"visual_tags":["financial_table"],"objects":[{"name":"半导体","value":"7337.87"}],'
                '"confidence_score":0.58}'
            ),
        }


def test_video_vision_service_keeps_keyframes_when_visual_model_unavailable():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="vision-fallback-test-", dir=temp_root))
    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"fake frame bytes")
    service = VideoVisionService(
        model_client=UnavailableVisualModel(),
        ocr_service=EmptyOcrService(),
    )
    transcript = {
        "segments": [
            {
                "start_ms": 900,
                "end_ms": 1600,
                "text": "看这里，这个位置非常关键，先保留这张图。",
            }
        ]
    }
    frames = [
        {
            "frame_index": 1,
            "timestamp_ms": 1000,
            "image_path": str(image_path),
            "trigger_source": "cue",
        }
    ]

    try:
        insights = service.analyze_frames(metadata={"title": "测试视频"}, transcript=transcript, frames=frames)
        assert len(insights) == 1
        assert "关键帧" in insights[0]["visual_summary"]
        assert insights[0]["confidence_score"] > 0
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_video_vision_service_uses_ocr_guided_summary_when_image_input_is_unsupported():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="vision-ocr-guided-test-", dir=temp_root))
    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"fake frame bytes")
    model = TextOnlyVisualModel()
    service = VideoVisionService(
        model_client=model,
        ocr_service=RichOcrService(),
    )
    transcript = {
        "segments": [
            {
                "start_ms": 900,
                "end_ms": 1600,
                "text": "看这里，半导体和电气设备都在跌幅榜前面。",
            }
        ]
    }
    frames = [
        {
            "frame_index": 1,
            "timestamp_ms": 1000,
            "image_path": str(image_path),
            "trigger_source": "cue",
        },
        {
            "frame_index": 2,
            "timestamp_ms": 1100,
            "image_path": str(image_path),
            "trigger_source": "cue",
        },
    ]

    try:
        insights = service.analyze_frames(metadata={"title": "测试视频"}, transcript=transcript, frames=frames)
        assert len(insights) == 2
        assert "跌幅榜前列" in insights[0]["visual_summary"]
        assert insights[0]["themes"] == ["半导体"]
        assert insights[0]["symbols"] == ["880491", "880446"]
        assert model.create_calls == 1
        assert model.complete_calls == 2
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

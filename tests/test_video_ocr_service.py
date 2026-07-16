from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from engines.content.video_ocr_service import VideoOcrService


def test_video_ocr_service_extracts_grouped_lines_from_paddleocr_result(monkeypatch):
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="paddleocr-test-", dir=temp_root))
    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"fake frame bytes")

    service = VideoOcrService(backend="paddleocr")
    monkeypatch.setattr(service, "_paddleocr_available", lambda: True)
    monkeypatch.setattr(
        service,
        "_get_paddleocr_engine",
        lambda: type(
            "FakePaddleEngine",
            (),
            {
                "predict": lambda self, image: [
                    {
                        "rec_texts": ["880446", "电气设备", "2048.01", "-37.72", "低分噪声"],
                        "rec_scores": [0.99, 0.98, 0.97, 0.96, 0.10],
                        "rec_boxes": [
                            [45, 213, 98, 231],
                            [97, 211, 172, 232],
                            [348, 213, 405, 230],
                            [431, 213, 476, 231],
                            [10, 500, 30, 520],
                        ],
                    }
                ]
            },
        )(),
    )

    try:
        text = service.extract_text(image_path)
        assert "880446 电气设备 2048.01 -37.72" in text
        assert "低分噪声" not in text
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_video_ocr_service_raises_when_paddleocr_is_unavailable(monkeypatch):
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="paddleocr-missing-test-", dir=temp_root))
    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"fake frame bytes")

    service = VideoOcrService(backend="paddleocr")
    monkeypatch.setattr(service, "_paddleocr_available", lambda: False)

    try:
        with pytest.raises(RuntimeError, match="PaddleOCR runtime is not installed"):
            service.extract_text(image_path)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_video_ocr_service_raises_when_paddleocr_prediction_fails(monkeypatch):
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="paddleocr-fail-test-", dir=temp_root))
    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"fake frame bytes")

    service = VideoOcrService(backend="paddleocr")
    monkeypatch.setattr(service, "_paddleocr_available", lambda: True)
    monkeypatch.setattr(
        service,
        "_get_paddleocr_engine",
        lambda: type(
            "FailingPaddleEngine",
            (),
            {"predict": lambda self, image: (_ for _ in ()).throw(RuntimeError("cuda dll missing"))},
        )(),
    )

    try:
        with pytest.raises(RuntimeError, match="PaddleOCR prediction failed"):
            service.extract_text(image_path)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_video_ocr_service_prefers_gpu_when_nvidia_env_is_present(monkeypatch):
    service = VideoOcrService(backend="paddleocr")
    monkeypatch.setenv("NVIDIA_VISIBLE_DEVICES", "all")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    assert service._resolve_paddle_device("auto") == "gpu:0"

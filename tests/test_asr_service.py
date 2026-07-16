from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from engines.content.asr_service import AsrService


def test_asr_service_prefers_cuda_when_available(monkeypatch):
    monkeypatch.setattr(AsrService, "_cuda_available", staticmethod(lambda: True))
    monkeypatch.delenv("ASR_USE_BATCHED", raising=False)
    monkeypatch.delenv("ASR_BATCH_SIZE", raising=False)
    monkeypatch.delenv("ASR_CHUNK_LENGTH_SECONDS", raising=False)
    monkeypatch.delenv("ASR_BEAM_SIZE", raising=False)
    monkeypatch.delenv("ASR_BEST_OF", raising=False)
    monkeypatch.delenv("ASR_CONDITION_ON_PREVIOUS_TEXT", raising=False)
    service = AsrService(model_size="small", device="auto", compute_type="auto")
    assert service.device == "cuda"
    assert service.compute_type == "float16"
    assert service.use_batched is True
    assert service.batch_size == 16
    assert service.chunk_length_seconds == 30
    assert service.beam_size == 3


def test_asr_service_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(AsrService, "_cuda_available", staticmethod(lambda: False))
    monkeypatch.delenv("ASR_USE_BATCHED", raising=False)
    service = AsrService(model_size="small", device="auto", compute_type="auto")
    assert service.device == "cpu"
    assert service.compute_type == "int8"
    assert service.use_batched is False
    assert service.batch_size == 1


def test_asr_service_uses_batched_pipeline_on_cuda(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    class FakeSegment:
        def __init__(self, start, end, text):
            self.start = start
            self.end = end
            self.text = text

    class FakeInfo:
        language = "zh"
        duration = 12.0

    class FakeWhisperModel:
        def __init__(self, model_size, device, compute_type):
            calls["model_init"] = (model_size, device, compute_type)

        def transcribe(self, *args, **kwargs):
            calls["model_transcribe"] = {"args": args, "kwargs": kwargs}
            return [], FakeInfo()

    class FakeBatchedInferencePipeline:
        def __init__(self, model):
            calls["pipeline_model"] = model

        def transcribe(self, audio_path, **kwargs):
            calls["pipeline_transcribe"] = {"audio_path": audio_path, "kwargs": kwargs}
            return [FakeSegment(0.0, 1.2, "测试片段")], FakeInfo()

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(
            WhisperModel=FakeWhisperModel,
            BatchedInferencePipeline=FakeBatchedInferencePipeline,
        ),
    )
    service = AsrService(
        model_size="large-v3",
        device="cuda",
        compute_type="float16",
        use_batched=True,
        batch_size=12,
        chunk_length_seconds=20,
        beam_size=1,
        best_of=1,
        condition_on_previous_text=False,
    )
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"audio")

    result = service.transcribe(audio_path, language_hint="zh")
    kwargs = calls["pipeline_transcribe"]["kwargs"]
    assert calls["model_init"] == ("large-v3", "cuda", "float16")
    assert kwargs["language"] == "zh"
    assert kwargs["batch_size"] == 12
    assert kwargs["chunk_length"] == 20
    assert kwargs["beam_size"] == 1
    assert kwargs["best_of"] == 1
    assert kwargs["without_timestamps"] is False
    assert kwargs["condition_on_previous_text"] is False
    assert result["use_batched"] is True
    assert result["batch_size"] == 12
    assert result["chunk_length_seconds"] == 20
    assert result["beam_size"] == 1


def test_asr_service_respects_env_overrides(monkeypatch):
    monkeypatch.setattr(AsrService, "_cuda_available", staticmethod(lambda: True))
    monkeypatch.setenv("ASR_USE_BATCHED", "true")
    monkeypatch.setenv("ASR_BATCH_SIZE", "20")
    monkeypatch.setenv("ASR_CHUNK_LENGTH_SECONDS", "18")
    monkeypatch.setenv("ASR_BEAM_SIZE", "1")
    monkeypatch.setenv("ASR_BEST_OF", "1")
    monkeypatch.setenv("ASR_CONDITION_ON_PREVIOUS_TEXT", "false")
    service = AsrService(model_size="small", device="auto", compute_type="auto")
    assert service.batch_size == 20
    assert service.chunk_length_seconds == 18
    assert service.beam_size == 1
    assert service.best_of == 1
    assert service.condition_on_previous_text is False


def test_asr_service_extends_linux_library_path(monkeypatch, tmp_path):
    nvidia_root = tmp_path / "nvidia"
    for relative in (
        ("cublas", "lib"),
        ("cudnn", "lib"),
        ("cuda_runtime", "lib"),
        ("cuda_nvrtc", "lib"),
        ("cu13", "lib"),
    ):
        nvidia_root.joinpath(*relative).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(AsrService, "_is_windows", staticmethod(lambda: False))
    monkeypatch.setattr("engines.content.asr_service.sys.path", [str(tmp_path)])
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/path")

    AsrService._ensure_nvidia_library_path()

    resolved = os.environ["LD_LIBRARY_PATH"]
    assert str(nvidia_root / "cublas" / "lib") in resolved
    assert str(nvidia_root / "cudnn" / "lib") in resolved
    assert str(nvidia_root / "cuda_runtime" / "lib") in resolved
    assert str(nvidia_root / "cuda_nvrtc" / "lib") in resolved
    assert "/existing/path" in resolved

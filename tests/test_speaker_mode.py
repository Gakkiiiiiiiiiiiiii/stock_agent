from __future__ import annotations

import sys
import tomllib
from types import SimpleNamespace

import pytest

from engines.content.asr_service import AsrService
from engines.content.diarization_service import DiarizationService
from engines.content.temporal_window_builder import TemporalWindowBuilder
from engines.content.transcript_postprocessor import TranscriptPostprocessor
from financial_agent.utils import project_root


def _make_transcript(text: str = "测试口播") -> dict:
    return {
        "text": text,
        "segments": [{"start_ms": 0, "end_ms": 1000, "text": text, "speaker_label": None}],
    }


# --- §85 Speaker 组：依赖与 token 配置闭环 ---


def test_diarization_dependency_available_in_media_extra():
    pyproject = tomllib.loads((project_root() / "pyproject.toml").read_text(encoding="utf-8"))
    media = pyproject["project"]["optional-dependencies"]["media"]
    assert any(str(item).lower().startswith("pyannote.audio") for item in media)


def test_diarization_token_env_name_matches_config(monkeypatch):
    monkeypatch.setenv("PYANNOTE_TOKEN", "pyannote-token")
    monkeypatch.setenv("HF_TOKEN", "hf-token")
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "huggingface-token")
    assert DiarizationService().auth_token == "pyannote-token"

    monkeypatch.delenv("PYANNOTE_TOKEN")
    assert DiarizationService().auth_token == "hf-token"

    monkeypatch.delenv("HF_TOKEN")
    assert DiarizationService().auth_token == "huggingface-token"


# --- speaker_mode 语义：不伪造 speaker_0 ---


def test_diarize_failure_never_fakes_speaker_0():
    try:
        import pyannote.audio  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("pyannote 已安装，无法模拟依赖缺失")
    service = DiarizationService()

    result = service.annotate("dummy.wav", _make_transcript(), speaker_mode="DIARIZE")

    assert result["diarization"]["status"] == "UNAVAILABLE"
    for segment in result["segments"]:
        assert segment["speaker_label"] == "speaker_unknown"
        assert segment["speaker_id"] == "speaker_unknown"
        assert segment["speaker_label"] != "speaker_0"


def test_diarize_runtime_error_marks_failed(monkeypatch):
    service = DiarizationService()
    monkeypatch.setattr(service, "_diarize", lambda audio_path: ([], "FAILED"))

    result = service.annotate("dummy.wav", _make_transcript(), speaker_mode="DIARIZE")

    assert result["diarization"]["status"] == "FAILED"
    assert all(segment["speaker_label"] == "speaker_unknown" for segment in result["segments"])


def test_unknown_mode_clears_speaker_labels():
    transcript = _make_transcript()
    transcript["segments"][0]["speaker_label"] = "speaker_0"

    result = DiarizationService().annotate("dummy.wav", transcript, speaker_mode="UNKNOWN")

    assert result["diarization"]["status"] == "DISABLED"
    for segment in result["segments"]:
        assert segment["speaker_label"] is None
        assert segment["speaker_id"] is None


def test_single_speaker_mode_marks_speaker_0():
    result = DiarizationService().annotate("dummy.wav", _make_transcript(), speaker_mode="SINGLE_SPEAKER")

    assert result["diarization"]["status"] == "SINGLE_SPEAKER"
    assert all(segment["speaker_label"] == "speaker_0" for segment in result["segments"])


# --- ASR：speaker_mode + asr_quality_proxy + word 概率维度 ---


class _FakeWord:
    def __init__(self, start, end, word, probability):
        self.start = start
        self.end = end
        self.word = word
        self.probability = probability


class _FakeSegment:
    def __init__(self):
        self.start = 0.0
        self.end = 1.0
        self.text = "黄金上涨"
        self.avg_logprob = -0.2
        self.no_speech_prob = 0.1
        self.compression_ratio = 1.1
        self.words = [_FakeWord(0.0, 0.5, "黄金", 0.9), _FakeWord(0.5, 1.0, "上涨", 0.7)]


class _FakeInfo:
    language = "zh"
    duration = 1.0


def _transcribe_with_fake_whisper(monkeypatch, tmp_path, speaker_mode):
    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return [_FakeSegment()], _FakeInfo()

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel, BatchedInferencePipeline=None),
    )
    service = AsrService(model_size="large-v3", device="cpu", compute_type="int8", use_batched=False)
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"audio")
    return service.transcribe(audio_path, language_hint="zh", speaker_mode=speaker_mode)


def test_asr_unknown_mode_does_not_fake_speaker(monkeypatch, tmp_path):
    result = _transcribe_with_fake_whisper(monkeypatch, tmp_path, "UNKNOWN")

    segment = result["segments"][0]
    assert segment["speaker_label"] is None
    assert result["speaker_mode"] == "UNKNOWN"


def test_asr_single_speaker_mode_marks_speaker_0(monkeypatch, tmp_path):
    result = _transcribe_with_fake_whisper(monkeypatch, tmp_path, "SINGLE_SPEAKER")

    assert result["segments"][0]["speaker_label"] == "speaker_0"


def test_asr_quality_proxy_and_word_probability_dimensions(monkeypatch, tmp_path):
    result = _transcribe_with_fake_whisper(monkeypatch, tmp_path, "UNKNOWN")

    segment = result["segments"][0]
    assert segment["asr_quality_proxy"] == segment["confidence_score"]
    assert segment["asr_quality_proxy"] is not None
    assert segment["mean_word_probability"] == pytest.approx(0.8, abs=1e-4)
    assert segment["min_word_probability"] == pytest.approx(0.7, abs=1e-4)


def test_asr_word_probability_none_without_word_data(monkeypatch, tmp_path):
    class NoWordSegment(_FakeSegment):
        def __init__(self):
            super().__init__()
            self.words = []

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return [NoWordSegment()], _FakeInfo()

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel, BatchedInferencePipeline=None),
    )
    service = AsrService(model_size="large-v3", device="cpu", compute_type="int8", use_batched=False)
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"audio")

    segment = service.transcribe(audio_path)["segments"][0]
    assert segment["mean_word_probability"] is None
    assert segment["min_word_probability"] is None


# --- correction_trace：所有变换类型可审计 ---


def test_correction_trace_records_multiple_types():
    postprocessor = TranscriptPostprocessor()

    result = postprocessor.normalize({"text": "K 线 百分之 5 呃 军量线 1 2 元", "segments": [{"start_ms": 0, "end_ms": 1000, "text": "K 线 百分之 5 呃 军量线 1 2 元"}]})

    trace = result["segments"][0]["correction_trace"]
    types = {entry["type"] for entry in trace}
    methods = {entry["method"] for entry in trace}
    assert "DICTIONARY_CORRECTION" in types
    assert "FORMAT_NORMALIZATION" in types
    assert "percent_wording" in methods
    assert "numeric_space_merge" in methods
    assert "filler_word_removal" in methods
    assert "term_dictionary" in methods
    assert all(entry["confidence"] == 1.0 for entry in trace)
    assert all({"type", "from", "to", "method", "confidence"} <= set(entry) for entry in trace)


def test_correction_trace_records_script_conversion():
    postprocessor = TranscriptPostprocessor()
    if postprocessor._get_opencc_converter() is None:
        pytest.skip("opencc 未安装")

    result = postprocessor.normalize({"text": "槓桿", "segments": [{"start_ms": 0, "end_ms": 1000, "text": "槓桿"}]})

    trace = result["segments"][0]["correction_trace"]
    script_entries = [entry for entry in trace if entry["type"] == "SCRIPT_CONVERSION"]
    assert script_entries
    assert script_entries[0]["method"] == "opencc_t2s"
    assert script_entries[0]["from"] == "槓桿"
    assert script_entries[0]["to"] == "杠杆"


# --- TemporalWindow：ASR 与 vision confidence 分离 ---


def test_window_confidence_excludes_vision_confidence():
    windows = TemporalWindowBuilder().build(
        {"segments": [{"start_ms": 0, "end_ms": 1000, "text": "测试", "confidence_score": 0.8}]},
        frame_insights=[{"timestamp_ms": 500, "confidence_score": 0.4}],
    )

    window = windows[0]
    assert window["confidence_score"] == pytest.approx(0.8, abs=1e-4)
    assert window["vision_confidence_score"] == pytest.approx(0.4, abs=1e-4)


def test_window_vision_confidence_none_without_frames():
    windows = TemporalWindowBuilder().build(
        {"segments": [{"start_ms": 0, "end_ms": 1000, "text": "测试", "confidence_score": 0.8}]}
    )

    assert windows[0]["vision_confidence_score"] is None

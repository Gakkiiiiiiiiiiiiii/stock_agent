from __future__ import annotations

import os
from pathlib import Path


class DiarizationService:
    """Time-overlap speaker attribution backed by pyannote when configured.

    The former implementation labelled every segment ``speaker_0``.  This
    implementation never pretends diarization occurred: if the optional model
    is unavailable it returns ``speaker_unknown`` and marks the transcript as
    degraded.
    """

    def __init__(self, model_name: str | None = None, auth_token: str | None = None) -> None:
        self.model_name = model_name or os.getenv("DIARIZATION_MODEL", "pyannote/speaker-diarization-3.1")
        # Token 读取顺序与 .env.example 对齐：PYANNOTE_TOKEN 优先，回退 HF 系变量。
        self.auth_token = (
            auth_token
            or os.getenv("PYANNOTE_TOKEN")
            or os.getenv("HF_TOKEN")
            or os.getenv("HUGGINGFACE_TOKEN")
        )
        self._pipeline = None

    def annotate(self, audio_path: str | Path, transcript: dict, speaker_mode: str = "DIARIZE") -> dict:
        mode = str(speaker_mode or "UNKNOWN").strip().upper()
        if mode == "SINGLE_SPEAKER":
            return transcript | {
                "segments": [dict(segment) | {"speaker_label": "speaker_0", "speaker_id": "speaker_0"} for segment in transcript.get("segments", [])],
                "diarization": {"status": "SINGLE_SPEAKER", "model": None, "speaker_mode": mode},
            }
        if mode != "DIARIZE":
            # UNKNOWN / 未启用：不做说话人识别，也不伪造 speaker_0。
            return transcript | {
                "segments": [dict(segment) | {"speaker_label": None, "speaker_id": None} for segment in transcript.get("segments", [])],
                "diarization": {"status": "DISABLED", "model": self.model_name, "speaker_mode": mode},
            }
        turns, failure_status = self._diarize(audio_path)
        if not turns:
            return transcript | {
                "segments": [dict(segment) | {"speaker_label": "speaker_unknown", "speaker_id": "speaker_unknown"} for segment in transcript.get("segments", [])],
                "diarization": {"status": failure_status or "FAILED", "model": self.model_name, "speaker_mode": mode},
            }
        segments = []
        for segment in transcript.get("segments", []):
            item = dict(segment)
            speaker, overlap = self._speaker_for_span(int(item.get("start_ms") or 0), int(item.get("end_ms") or item.get("start_ms") or 0), turns)
            item["speaker_label"] = speaker
            item["speaker_id"] = speaker
            item["speaker_attribution_confidence"] = overlap
            segments.append(item)
        return transcript | {"segments": segments, "diarization": {"status": "COMPLETED", "model": self.model_name, "speaker_mode": mode, "speaker_count": len({turn[2] for turn in turns})}}

    def _diarize(self, audio_path: str | Path) -> tuple[list[tuple[int, int, str]], str | None]:
        try:
            from pyannote.audio import Pipeline
        except ImportError:
            return [], "UNAVAILABLE"
        try:
            if self._pipeline is None:
                kwargs = {"use_auth_token": self.auth_token} if self.auth_token else {}
                self._pipeline = Pipeline.from_pretrained(self.model_name, **kwargs)
            result = self._pipeline(str(audio_path))
            diarization = getattr(result, "speaker_diarization", result)
            turns = [(int(turn.start * 1000), int(turn.end * 1000), str(speaker)) for turn, _, speaker in diarization.itertracks(yield_label=True)]
            return turns, None if turns else "FAILED"
        except Exception:
            return [], "FAILED"

    @staticmethod
    def _speaker_for_span(start_ms: int, end_ms: int, turns: list[tuple[int, int, str]]) -> tuple[str, float]:
        duration = max(1, end_ms - start_ms)
        overlaps: dict[str, int] = {}
        for turn_start, turn_end, speaker in turns:
            overlap = max(0, min(end_ms, turn_end) - max(start_ms, turn_start))
            if overlap:
                overlaps[speaker] = overlaps.get(speaker, 0) + overlap
        if not overlaps:
            return "speaker_unknown", 0.0
        speaker, overlap = max(overlaps.items(), key=lambda item: item[1])
        return speaker, round(overlap / duration, 4)

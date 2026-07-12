from __future__ import annotations

from pathlib import Path


class DiarizationService:
    def annotate(self, audio_path: str | Path, transcript: dict) -> dict:
        _ = audio_path
        segments = []
        for segment in transcript.get("segments", []):
            normalized = dict(segment)
            normalized.setdefault("speaker_label", "speaker_0")
            segments.append(normalized)
        return transcript | {"segments": segments}


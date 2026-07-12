from __future__ import annotations

import os
import subprocess
from pathlib import Path


class AudioPipeline:
    def __init__(self, ffmpeg_bin: str | None = None) -> None:
        self.ffmpeg_bin = ffmpeg_bin or os.getenv("FFMPEG_BIN", "ffmpeg")
        default_ffprobe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        self.ffprobe_bin = os.getenv("FFPROBE_BIN") or default_ffprobe

    def standardize_audio(self, input_path: str | Path, output_dir: str | Path) -> Path:
        source = Path(input_path)
        if not source.exists():
            raise FileNotFoundError(source)
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{source.stem}_16k_mono.wav"
        subprocess.run(
            [
                self.ffmpeg_bin,
                "-y",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-vn",
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return target

    def probe_duration_seconds(self, input_path: str | Path) -> float:
        source = Path(input_path)
        if not source.exists():
            raise FileNotFoundError(source)
        result = subprocess.run(
            [
                self.ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float((result.stdout or "0").strip() or 0.0)

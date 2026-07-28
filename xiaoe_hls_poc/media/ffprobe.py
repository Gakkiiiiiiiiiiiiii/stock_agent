"""ffprobe JSON 解析与输出验证(第 19 节)。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ErrorCode, PocError
from .ffmpeg import run_ffprobe


@dataclass
class ProbeResult:
    duration: float | None = None
    size: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    stream_count: int = 0
    has_video: bool = False
    has_audio: bool = False
    raw: dict = field(default_factory=dict)


def probe_file(path: Path) -> ProbeResult:
    proc = run_ffprobe(
        ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PocError(ErrorCode.FFMPEG_FAILED, "ffprobe 输出不是合法 JSON") from exc
    result = ProbeResult(raw=data)
    fmt = data.get("format", {})
    if fmt.get("duration"):
        result.duration = float(fmt["duration"])
    if fmt.get("size"):
        result.size = int(fmt["size"])
    for stream in data.get("streams", []):
        result.stream_count += 1
        if stream.get("codec_type") == "video":
            result.has_video = True
            result.video_codec = stream.get("codec_name")
            result.width = stream.get("width")
            result.height = stream.get("height")
        elif stream.get("codec_type") == "audio":
            result.has_audio = True
            result.audio_codec = stream.get("codec_name")
    return result


def duration_within_tolerance(actual: float | None, expected: float) -> bool:
    """阈值:max(3 秒, 1%)(19.2)。"""
    if actual is None or expected <= 0:
        return False
    return abs(actual - expected) <= max(3.0, expected * 0.01)


def decode_sample(path: Path, *, seek: float = 0.0, seconds: float = 5.0) -> bool:
    """首尾解码抽样(19.3):解码到 null,检查严重错误。"""
    from .ffmpeg import run_ffmpeg  # 延迟导入避免循环

    args = []
    if seek > 0:
        args += ["-ss", f"{seek:.3f}"]
    args += [
        "-t", f"{seconds:.3f}",
        "-i", str(path),
        "-f", "null", "-",
    ]
    import subprocess

    from ..config import locate_binary

    ffmpeg = locate_binary("ffmpeg")
    proc = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-nostdin", "-v", "error", *args],
        capture_output=True, text=True, timeout=300, shell=False,
    )
    return proc.returncode == 0

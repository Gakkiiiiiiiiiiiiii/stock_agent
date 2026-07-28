"""组装器(18.3):TS 用 ffconcat remux;fMP4 先拼 init+fragments 再交给 FFmpeg remux。"""

from __future__ import annotations

from pathlib import Path

from ..errors import ErrorCode, PocError
from ..models import SegmentTask
from .ffmpeg import run_ffmpeg


def _write_ffconcat(files: list[Path], dest: Path) -> None:
    lines = ["ffconcat version 1.0"]
    for f in files:
        escaped = str(f).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assemble_ts(plan: list[SegmentTask], job_dir: Path, output_part: Path) -> Path:
    """MPEG-TS:ffconcat + -c copy remux 到 MP4。"""
    plain_files = [Path(t.local_plain_path) for t in plan]
    missing = [f for f in plain_files if not f.is_file()]
    if missing:
        raise PocError(ErrorCode.FFMPEG_FAILED, f"缺少 {len(missing)} 个明文分片")
    concat_file = job_dir / "concat.ffconcat"
    _write_ffconcat(plain_files, concat_file)
    output_part.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            "-y", str(output_part),
        ]
    )
    return output_part


def assemble_fmp4(
    init_file: Path, plan: list[SegmentTask], job_dir: Path, output_part: Path
) -> Path:
    """fMP4:init + fragments 拼接为临时输入,再由 FFmpeg remux(12.5/18.3)。
    不把拼接结果直接作为最终产物。"""
    if not init_file.is_file():
        raise PocError(ErrorCode.FFMPEG_FAILED, "缺少 fMP4 初始化段")
    fragments = [Path(t.local_plain_path) for t in plan]
    missing = [f for f in fragments if not f.is_file()]
    if missing:
        raise PocError(ErrorCode.FFMPEG_FAILED, f"缺少 {len(missing)} 个 fMP4 分片")
    combined = job_dir / "combined.fmp4.tmp"
    with combined.open("wb") as out:
        out.write(init_file.read_bytes())
        for f in fragments:
            out.write(f.read_bytes())
    output_part.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i", str(combined),
            "-c", "copy",
            "-movflags", "+faststart",
            "-y", str(output_part),
        ]
    )
    return output_part

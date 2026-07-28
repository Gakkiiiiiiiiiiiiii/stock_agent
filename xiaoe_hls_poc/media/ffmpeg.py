"""FFmpeg 子进程封装(17.2/22.4):参数数组、禁 shell、stderr 脱敏、限时。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import MAX_FFMPEG_SECONDS, locate_binary
from ..errors import ErrorCode, PocError
from ..security.redactor import redact_text, redact_url


def run_ffmpeg(
    args: list[str],
    *,
    timeout: int = MAX_FFMPEG_SECONDS,
    secret_urls: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """args 不含 'ffmpeg' 本体。禁止 shell=True。"""
    ffmpeg = locate_binary("ffmpeg")
    cmd = [str(ffmpeg), "-hide_banner", "-nostdin", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PocError(ErrorCode.FFMPEG_FAILED, f"FFmpeg 执行超时({timeout}s)") from exc
    if proc.returncode != 0:
        stderr = proc.stderr or ""
        for u in secret_urls or []:
            stderr = stderr.replace(u, redact_url(u))
        raise PocError(
            ErrorCode.FFMPEG_FAILED,
            f"FFmpeg 失败(exit {proc.returncode}): {redact_text(stderr)[-800:]}",
        )
    return proc


def run_ffprobe(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    ffprobe = locate_binary("ffprobe")
    cmd = [str(ffprobe), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise PocError(ErrorCode.FFMPEG_FAILED, "ffprobe 执行超时") from exc
    if proc.returncode != 0:
        raise PocError(
            ErrorCode.FFMPEG_FAILED,
            f"ffprobe 失败: {redact_text((proc.stderr or '')[-400:])}",
        )
    return proc


def atomic_replace(src: Path, dst: Path) -> None:
    src.replace(dst)

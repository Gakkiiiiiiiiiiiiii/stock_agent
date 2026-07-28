"""Engine A:FFmpeg Direct(第 17 节)。

把合法 M3U8 + Header/Cookie 交给 FFmpeg 原生 HLS demuxer。
参数数组 subprocess,禁 shell,-c copy,.part.mp4 原子改名,stderr 脱敏。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from ..download.state_store import sha256_file
from ..errors import ErrorCode, PocError
from ..input.request_loader import EffectiveContext
from ..media.ffprobe import duration_within_tolerance, probe_file
from ..media.ffmpeg import run_ffmpeg
from ..models import DownloadReport
from ..security.redactor import redact_url
from ..auth.cookie_adapter import cookie_header_for_url

MIN_OUTPUT_BYTES = 1024


def _build_cookie_header(ctx: EffectiveContext) -> str | None:
    if not ctx.cookies:
        return None
    header = cookie_header_for_url(ctx.cookies, ctx.url_secret)
    return header or None


def run_ffmpeg_direct(
    ctx: EffectiveContext,
    output: Path,
    *,
    timeout: int = 6 * 3600,
    expected_duration: float | None = None,
    headers: dict[str, str] | None = None,
    progress_cb=None,
) -> DownloadReport:
    job_id = uuid.uuid4().hex[:12]
    report = DownloadReport(
        job_id=job_id,
        source_mode=ctx.source_mode,
        capture_id=ctx.capture_id,
        engine="ffmpeg-direct",
    )
    headers = headers if headers is not None else ctx.headers

    part = output.with_suffix(output.suffix + ".part.mp4")
    args: list[str] = []
    ua = headers.get("User-Agent") or headers.get("user-agent")
    if ua:
        args += ["-user_agent", ua]
    referer = headers.get("Referer") or headers.get("referer") or ctx.page_url
    if referer:
        args += ["-referer", referer]

    extra_header_lines: list[str] = []
    origin = headers.get("Origin") or headers.get("origin")
    if origin:
        extra_header_lines.append(f"Origin: {origin}")
    if ctx.authorization:
        extra_header_lines.append(f"Authorization: {ctx.authorization}")
    cookie_header = _build_cookie_header(ctx)
    if cookie_header:
        extra_header_lines.append(f"Cookie: {cookie_header}")
    if extra_header_lines:
        args += ["-headers", "\r\n".join(extra_header_lines) + "\r\n"]

    args += [
        "-rw_timeout", "30000000",
        "-reconnect", "1",
        "-reconnect_on_network_error", "1",
        "-reconnect_on_http_error", "429,500,502,503,504",
        "-reconnect_delay_max", "8",
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
        "-i", ctx.url_secret,
        "-map", "0:v?", "-map", "0:a?",
        "-c", "copy",
        "-movflags", "+faststart",
        "-y", str(part),
    ]

    t0 = time.monotonic()
    try:
        run_ffmpeg(args, timeout=timeout, secret_urls=[ctx.url_secret])
    except PocError:
        part.unlink(missing_ok=True)
        raise
    report.metrics["ffmpeg_seconds"] = round(time.monotonic() - t0, 3)

    if not part.is_file() or part.stat().st_size < MIN_OUTPUT_BYTES:
        part.unlink(missing_ok=True)
        raise PocError(ErrorCode.FFMPEG_FAILED, "FFmpeg 退出码为 0 但输出缺失或过小")

    probe = probe_file(part)
    if not probe.has_video and not probe.has_audio:
        part.unlink(missing_ok=True)
        raise PocError(ErrorCode.OUTPUT_NO_VIDEO, "输出无任何音视频流")

    report.output_duration = probe.duration
    report.output_size = probe.size
    if expected_duration:
        report.estimated_duration = expected_duration
        if duration_within_tolerance(probe.duration, expected_duration):
            report.validation_status = "PASSED"
        else:
            report.validation_status = "DURATION_MISMATCH"
            report.warnings.append(
                f"时长偏差: 预期 {expected_duration:.1f}s 实际 {probe.duration or 0:.1f}s"
            )
    else:
        report.validation_status = "PASSED"

    output.parent.mkdir(parents=True, exist_ok=True)
    part.replace(output)  # 原子改名(17.2)
    report.metrics["output_sha256"] = sha256_file(output)
    return report

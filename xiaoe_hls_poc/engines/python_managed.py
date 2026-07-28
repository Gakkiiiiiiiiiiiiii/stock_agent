"""Engine B:Python Managed(第 18 节)。

Python 完整托管:Playlist 解析 -> Variant -> 分片计划 -> 并发下载
-> AES 解密 -> 分片校验 -> ffconcat remux -> ffprobe 验证。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import httpx

from .. import config
from ..crypto.aes128 import decrypt_aes128_cbc
from ..crypto.key_manager import KeyManager
from ..download.scheduler import Scheduler
from ..download.segment_downloader import download_segment
from ..download.state_store import StateStore, can_reuse_segment, sha256_file
from ..errors import ErrorCode, PocError
from ..hls.iv_strategy import resolve_iv
from ..hls.parser import MediaPlaylist, parse_playlist
from ..hls.resolver import ResolvedPlaylist, fetch_playlist_text, resolve_playlist
from ..hls.segment_planner import build_plan
from ..http.client_factory import build_client
from ..http.retry_policy import RetryPolicy
from ..input.request_loader import EffectiveContext
from ..media.assembler import assemble_fmp4, assemble_ts
from ..media.ffprobe import duration_within_tolerance, probe_file
from ..media.segment_validator import probe_media
from ..models import DownloadReport, DownloadRequest, SegmentTask
from ..security.redactor import redact_url, url_fingerprint


def run_python_managed(
    ctx: EffectiveContext,
    request: DownloadRequest,
    *,
    jobs_base: Path | None = None,
    progress_cb=None,
) -> DownloadReport:
    job_id = uuid.uuid4().hex[:12]
    report = DownloadReport(
        job_id=job_id,
        source_mode=ctx.source_mode,
        capture_id=ctx.capture_id,
        engine="python-managed",
        auth_status="UNKNOWN",
    )
    output = Path(request.output_path)
    job_dir = (jobs_base or config.jobs_dir()) / job_id
    for sub in ("playlist", "encrypted", "plain", "init"):
        (job_dir / sub).mkdir(parents=True, exist_ok=True)

    client = build_client(
        headers=ctx.headers,
        cookies=ctx.cookies,
        authorization=ctx.authorization,
        authorized_host=ctx.authorized_host,
        target_url=ctx.url_secret,
        timeout=request.timeout_seconds,
    )
    try:
        # 1. Playlist 解析
        t0 = time.monotonic()
        text = fetch_playlist_text(client, ctx.url_secret)
        (job_dir / "playlist" / "source.m3u8").write_text(text, encoding="utf-8")

        # 已取回文本时直接解析;Master 交给 resolver(内部获取子播放列表)
        parsed = parse_playlist(text, ctx.url_secret)
        if isinstance(parsed, MediaPlaylist):
            resolved = ResolvedPlaylist(media=parsed, playlist_type="media")
        else:
            resolved = resolve_playlist(client, ctx.url_secret, quality=request.quality)
        media = resolved.media
        report.playlist_type = resolved.playlist_type
        report.variant = resolved.variant
        report.encryption_method = media.encryption_method
        report.estimated_duration = media.total_duration
        report.metrics["playlist_seconds"] = round(time.monotonic() - t0, 3)

        # 2. 分片计划 + 续传状态
        plan = build_plan(media, job_dir, probe_segments=request.probe_segments)
        report.segment_total = len(plan)
        from ..download.state_store import sha256_bytes

        playlist_hash = sha256_bytes(text.encode("utf-8"))
        state = StateStore(job_dir)
        had_state = state.load()
        state_compatible = had_state and state.playlist_hash == playlist_hash
        state.playlist_hash = playlist_hash

        # 3. 下载 + 解密 + 校验
        retry = RetryPolicy(max_attempts=request.retries + 1)
        key_manager = KeyManager(
            lambda u: _fetch_key(client, u), max_keys=64
        )
        scheduler = Scheduler(workers=request.workers)

        def worker_factory() -> httpx.Client:
            return build_client(
                headers=ctx.headers,
                cookies=ctx.cookies,
                authorization=ctx.authorization,
                authorized_host=ctx.authorized_host,
                target_url=ctx.url_secret,
                timeout=request.timeout_seconds,
            )

        def handler(wclient: httpx.Client, task: SegmentTask) -> int:
            return _process_segment(
                wclient, task, retry, key_manager, request.iv_strategy,
                state, state_compatible, request.resume,
            )

        failures: list[tuple[SegmentTask, Exception]] = []
        completed = 0

        def on_result(task, result, exc):
            nonlocal completed
            if exc is not None:
                failures.append((task, exc))
                st = state.get(task.index)
                st.status = "failed"
                st.error_code = getattr(exc, "code", None) and exc.code.value or "INTERNAL_ERROR"
            else:
                completed += 1
            state.save()
            if progress_cb:
                progress_cb(task, exc)

        scheduler.run(plan, worker_factory, handler, on_result)

        if failures:
            report.segment_success = completed
            report.segment_failed = len(failures)
            first_task, first_exc = failures[0]
            code = getattr(first_exc, "code", ErrorCode.INTERNAL_ERROR)
            raise PocError(
                code,
                f"{len(failures)} 个分片失败,首个: 分片 {first_task.index} - "
                f"{redact_url(getattr(first_exc, 'message', str(first_exc)))}",
            )
        report.segment_success = completed

        # 4. 组装
        t1 = time.monotonic()
        part = output.with_suffix(output.suffix + ".part.mp4")
        is_fmp4 = media.map_uri is not None or (
            plan and probe_media(Path(plan[0].local_plain_path).read_bytes()) == "fmp4"
        )
        if is_fmp4:
            init_file = download_init_segment(
                client, media, job_dir, key_manager, request.iv_strategy
            )
            if init_file is None or not init_file.is_file():
                raise PocError(ErrorCode.FFMPEG_FAILED, "fMP4 初始化段缺失")
            assemble_fmp4(init_file, plan, job_dir, part)
        else:
            assemble_ts(plan, job_dir, part)
        report.metrics["assemble_seconds"] = round(time.monotonic() - t1, 3)

        # 5. 输出验证 + 原子改名
        probe = probe_file(part)
        report.output_duration = probe.duration
        report.output_size = probe.size
        if not probe.has_video and not probe.has_audio:
            part.unlink(missing_ok=True)
            report.validation_status = "FAILED"
            raise PocError(ErrorCode.OUTPUT_NO_VIDEO, "输出无音视频流")
        expected = sum(t.duration for t in plan) if request.probe_segments else media.total_duration
        if duration_within_tolerance(probe.duration, expected):
            report.validation_status = "PASSED"
        else:
            report.validation_status = "DURATION_MISMATCH"
            report.warnings.append(
                f"时长偏差: 预期 {expected:.1f}s 实际 {probe.duration or 0:.1f}s"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        part.replace(output)
        report.metrics["output_sha256"] = sha256_file(output)

        # 6. 清理明文临时文件(14.5)
        if not request.keep_temp:
            _cleanup_plaintext(job_dir)
        return report
    finally:
        client.close()


def _fetch_key(client: httpx.Client, url: str) -> bytes:
    resp = client.get(url)
    if resp.status_code >= 400:
        raise PocError(
            ErrorCode.KEY_HTTP_ERROR, f"Key HTTP {resp.status_code}: {redact_url(url)}"
        )
    return resp.content


def _process_segment(
    client: httpx.Client,
    task: SegmentTask,
    retry: RetryPolicy,
    key_manager: KeyManager,
    iv_strategy: str,
    state: StateStore,
    state_compatible: bool,
    resume: bool,
) -> int:
    enc_path = Path(task.local_encrypted_path)
    plain_path = Path(task.local_plain_path)
    st = state.get(task.index)
    st.uri_hash = url_fingerprint(task.uri_secret)

    # Resume:明文已验证则直接复用(16.3)
    if resume and state_compatible and can_reuse_segment(
        st, task, plain_path,
        playlist_hash_current=state.playlist_hash,
        playlist_hash_saved=state.playlist_hash,
    ):
        return st.bytes_downloaded

    size = download_segment(client, task, enc_path, retry)
    st.attempts += 1
    st.status = "downloaded"
    st.bytes_downloaded = size

    kc = task.key_context
    data = enc_path.read_bytes()
    if kc and kc.method.upper() == "AES-128":
        key = key_manager.resolve(kc)
        iv = resolve_iv(iv_strategy, kc.explicit_iv, task.media_sequence, task.index)
        data = decrypt_aes128_cbc(key, iv, data)

    media_type = probe_media(data)
    if media_type is None:
        raise PocError(
            ErrorCode.DECRYPT_MEDIA_INVALID,
            f"分片 {task.index} 解密后未通过媒体探测",
        )

    plain_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = plain_path.with_suffix(plain_path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(plain_path)
    st.status = "verified"
    from ..download.state_store import sha256_bytes

    st.sha256 = sha256_bytes(data)
    return size


def download_init_segment(
    client: httpx.Client, media, job_dir: Path, key_manager: KeyManager, iv_strategy: str
) -> Path | None:
    """下载并按其 KeyContext 解密 EXT-X-MAP 初始化段(12.5)。"""
    if not media.map_uri:
        return None
    init_dir = job_dir / "init"
    init_dir.mkdir(parents=True, exist_ok=True)
    init_file = init_dir / "init.mp4"
    resp = client.get(media.map_uri)
    if resp.status_code >= 400:
        raise PocError(ErrorCode.SEGMENT_HTTP_ERROR, "初始化段下载失败")
    data = resp.content
    kc = media.map_key_context
    if kc and kc.method.upper() == "AES-128":
        key = key_manager.resolve(kc)
        iv = resolve_iv(
            iv_strategy, kc.explicit_iv, media.media_sequence_base, 0
        )
        data = decrypt_aes128_cbc(key, iv, data)
    init_file.write_bytes(data)
    return init_file


def _cleanup_plaintext(job_dir: Path) -> None:
    import shutil

    for sub in ("plain", "encrypted"):
        d = job_dir / sub
        if d.is_dir():
            shutil.rmtree(d)

"""命令行入口(第 8 节)。涉及内容访问的命令必须 --authorized-content。"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
from pathlib import Path

import typer

from . import __version__, config, models
from .errors import ErrorCode, PocError
from .security.authorization_guard import COMPLIANCE_NOTICE, require_authorized_content
from .security.path_policy import resolve_output_path
from .security.redactor import redact_url

app = typer.Typer(
    name="xiaoe-hls-poc",
    help="小鹅通已授权视频 HLS 下载可行性验证工具(PoC)。仅限本人已授权内容。",
    no_args_is_help=True,
)

console = typer.echo


def _fail(exc: PocError) -> None:
    console(f"[{exc.code.value}] {exc.message}", err=True)
    if exc.hint:
        console(f"提示: {exc.hint}", err=True)
    raise typer.Exit(code=1)


def _check_authorized(flag: bool) -> None:
    try:
        require_authorized_content(flag)
    except PocError as exc:
        _fail(exc)


# ---------------------------------------------------------------- doctor


@app.command()
def doctor() -> None:
    """环境自检:Python / Playwright / Chromium / FFmpeg / 目录 / 磁盘。"""
    ok_all = True

    def row(name: str, ok: bool, detail: str) -> None:
        nonlocal ok_all
        ok_all = ok_all and ok
        console(f"  [{'OK' if ok else 'FAIL'}] {name}: {detail}")

    console(f"xiaoe-hls-poc {__version__} doctor")
    console(COMPLIANCE_NOTICE)

    v = sys.version_info
    row("Python", v >= (3, 11), f"{v.major}.{v.minor}.{v.micro} ({platform.system()})")

    try:
        import playwright  # noqa: F401

        row("playwright 包", True, playwright.__file__ or "installed")
    except ImportError:
        row("playwright 包", False, "未安装: pip install playwright")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            exe = Path(p.chromium.executable_path)
            row(
                "Chromium", exe.is_file(),
                str(exe) if exe.is_file()
                else "未安装,执行 python -m playwright install chromium",
            )
    except Exception as exc:  # noqa: BLE001
        row("Chromium", False, f"检测失败: {exc}")

    for kind in ("ffmpeg", "ffprobe"):
        path = config.find_binary_or_none(kind)
        if path:
            try:
                import subprocess

                proc = subprocess.run(
                    [str(path), "-version"], capture_output=True, text=True, timeout=15
                )
                first = (proc.stdout or "").splitlines()[0] if proc.stdout else "?"
                row(kind, proc.returncode == 0, f"{path} | {first}")
            except Exception as exc:  # noqa: BLE001
                row(kind, False, f"{path} 执行失败: {exc}")
        else:
            row(kind, False, "未找到(XIAOE_FFMPEG / PATH / tools/ffmpeg/**/bin)")

    try:
        from Crypto.Cipher import AES  # noqa: F401

        row("PyCryptodome", True, "AES 可用")
    except ImportError:
        row("PyCryptodome", False, "未安装")

    try:
        config.ensure_runtime_dirs()
        home = config.home_dir()
        test = home / ".doctor-write-test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        row("运行期目录", True, str(home))
    except OSError as exc:
        row("运行期目录", False, f"不可写: {exc}")

    usage = shutil.disk_usage(Path.cwd())
    row("磁盘空间", usage.free > 1 << 30, f"可用 {usage.free / (1 << 30):.1f} GiB")

    import time

    skew = abs(time.time() - time.mktime(time.localtime()))
    row("系统时间", skew < 2, "本地时钟一致" if skew < 2 else f"偏差 {skew:.1f}s")

    try:
        import socket

        socket.setdefaulttimeout(5)
        socket.getaddrinfo("www.baidu.com", 443)
        row("DNS", True, "解析正常")
    except OSError as exc:
        row("DNS", False, f"解析失败: {exc}")

    raise typer.Exit(code=0 if ok_all else 1)


# ---------------------------------------------------------------- auth


@app.command()
def login(
    course_url: str = typer.Option(..., "--course-url", help="已授权课程页 URL"),
    profile: str = typer.Option("default", "--profile"),
    timeout: int = typer.Option(600, "--timeout"),
    authorized_content: bool = typer.Option(False, "--authorized-content"),
) -> None:
    """打开可见浏览器,由用户本人完成登录并保存独立 Profile。"""
    _check_authorized(authorized_content)
    from .auth import browser_auth
    from .security.url_policy import validate_url

    try:
        validate_url(course_url)
        ctx = browser_auth.interactive_login(course_url, profile, timeout_seconds=timeout)
    except PocError as exc:
        _fail(exc)
    console(f"登录完成: profile={ctx.profile_name}, 状态={ctx.login_status}")
    console("凭据已保存到受保护目录(不入库、不入日志)。下一步: capture 捕获播放地址。")


@app.command()
def capture(
    course_url: str = typer.Option(..., "--course-url"),
    profile: str = typer.Option("default", "--profile"),
    authorized_content: bool = typer.Option(False, "--authorized-content"),
) -> None:
    """监听课程页请求,捕获并保存 M3U8 候选(capture.json 脱敏)。"""
    _check_authorized(authorized_content)
    from .capture.network_capture import run_capture
    from .security.url_policy import validate_url

    try:
        validate_url(course_url)
        cap = run_capture(course_url, profile)
    except PocError as exc:
        _fail(exc)
    console(f"capture-id: {cap.capture_id}")


@app.command("session-status")
def session_status(profile: str = typer.Option("default", "--profile")) -> None:
    """查看登录会话状态(不显示任何 Cookie 值)。"""
    from .auth import profile_store, session_store
    from .auth.session_validator import classify_session_status

    exists = profile_store.profile_exists(profile)
    meta = session_store.load_session_meta(profile) or {}
    has_state = session_store.storage_state_path(profile).is_file()
    cookie_count = meta.get("cookie_count", 0)
    status, need_login = classify_session_status(
        profile_exists=exists, has_storage_state=has_state, cookie_count=cookie_count
    )
    console(f"profile: {profile}")
    console(f"  Profile 存在: {exists}")
    console(f"  storage state: {'存在' if has_state else '缺失'}")
    console(f"  最后授权时间: {meta.get('last_auth_at', '未知')}")
    console(f"  课程域名: {meta.get('course_domain', '未知')}")
    console(f"  Cookie 数量(不显示值): {cookie_count}")
    console(f"  会话状态: {status}")
    console(f"  需要重新登录: {'是' if need_login else '否'}")


@app.command("session-clear")
def session_clear(
    profile: str = typer.Option("default", "--profile"),
    yes: bool = typer.Option(False, "--yes", help="跳过确认"),
) -> None:
    """删除 Profile、storage state、capture 元数据;不删除已下载视频。"""
    if not yes:
        confirm = typer.confirm(f"确认清除 profile '{profile}' 的全部凭据?")
        if not confirm:
            console("已取消")
            raise typer.Exit(code=0)
    from .auth import profile_store, secret_store, session_store
    from .capture.capture_store import delete_capture, list_captures

    removed_profile = profile_store.delete_profile(profile)
    removed_session = session_store.clear_session(profile)
    removed_caps = 0
    for cap in list_captures():
        if cap.auth_context_id == profile:
            secret_store.delete_secret(cap.headers_secret_ref)
            delete_capture(cap.capture_id)
            removed_caps += 1
    console(
        f"已清理: profile={'是' if removed_profile else '无'}, "
        f"session={'是' if removed_session else '无'}, capture {removed_caps} 条"
    )


# ---------------------------------------------------------------- har


@app.command("har-inspect")
def har_inspect(har_file: str = typer.Argument(..., help="HAR 文件路径(仅本地处理)")) -> None:
    """列出 HAR 中的 M3U8 候选(脱敏显示)。"""
    from .input.har_loader import load_har

    try:
        entries = load_har(har_file)
    except PocError as exc:
        _fail(exc)
    if not entries:
        console("未发现 M3U8 候选请求")
        raise typer.Exit(code=1)
    console(f"共 {len(entries)} 个 M3U8 候选(均已脱敏):")
    for e in entries:
        row = e.redacted_row()
        console(
            f"  index={row['index']} status={row['status']} "
            f"ct={row['content_type'] or '-'} auth={row['has_authorization']} "
            f"extm3u={row['body_extm3u']}\n    {row['url']}"
        )
    console("提醒: HAR 含敏感凭据,使用完毕请安全删除原文件;本工具不保存 HAR 副本。")


# ---------------------------------------------------------------- probe / download

_SOURCE_KWARGS = dict(
    url=typer.Option(None, "--url", help="手工 M3U8 URL"),
    capture_id=typer.Option(None, "--capture-id"),
    course_url=typer.Option(None, "--course-url"),
    har=typer.Option(None, "--har", help="HAR 文件"),
    request_index=typer.Option(None, "--request-index"),
    profile=typer.Option("default", "--profile"),
    headers_file=typer.Option(None, "--headers-file"),
    cookie_env=typer.Option(None, "--cookie-env", help="Cookie 环境变量名(仅手工模式)"),
)


def _resolve_ctx(url, capture_id, course_url, har, request_index, profile,
                 headers_file, cookie_env):
    from .input.request_loader import resolve_effective_context

    return resolve_effective_context(
        url=url, capture_id=capture_id, course_url=course_url, profile=profile,
        headers_file=headers_file, cookie_env=cookie_env, har=har,
        request_index=request_index,
    )


@app.command()
def probe(
    url: str = _SOURCE_KWARGS["url"],
    capture_id: str = _SOURCE_KWARGS["capture_id"],
    course_url: str = _SOURCE_KWARGS["course_url"],
    har: str = _SOURCE_KWARGS["har"],
    request_index: int = _SOURCE_KWARGS["request_index"],
    profile: str = _SOURCE_KWARGS["profile"],
    headers_file: str = _SOURCE_KWARGS["headers_file"],
    cookie_env: str = _SOURCE_KWARGS["cookie_env"],
    quality: str = typer.Option("best", "--quality"),
    iv_strategy: str = typer.Option("hls-spec", "--iv-strategy"),
    timeout: int = typer.Option(30, "--timeout"),
    authorized_content: bool = typer.Option(False, "--authorized-content"),
) -> None:
    """最小协议验证:Playlist 类型、Variant、Key、首分片解密与媒体探测。"""
    _check_authorized(authorized_content)
    from .auth.session_validator import classify_http_error
    from .hls.probe_service import run_probe
    from .http.client_factory import build_client

    try:
        ctx = _resolve_ctx(url, capture_id, course_url, har, request_index,
                           profile, headers_file, cookie_env)
        console(f"来源: {ctx.source_mode} | {redact_url(ctx.url_secret)}")
        client = build_client(
            headers=ctx.headers, cookies=ctx.cookies,
            authorization=ctx.authorization, authorized_host=ctx.authorized_host,
            target_url=ctx.url_secret, timeout=timeout,
        )
        try:
            result = run_probe(
                client, ctx.url_secret, quality=quality, iv_strategy=iv_strategy,
                ffprobe_check=False,
            )
        finally:
            client.close()
    except PocError as exc:
        console(f"诊断分类: {classify_http_error(exc)}", err=True)
        _fail(exc)

    console(f"Playlist HTTP: {result['playlist_status']} | 类型: {result['playlist_type']}")
    if result.get("variant"):
        v = result["variant"]
        console(f"Variant: {v.get('width')}x{v.get('height')} bw={v.get('bandwidth')}")
    console(f"分片数: {result['segment_count']} | 预计时长: {result['estimated_duration']:.1f}s")
    console(f"Media Sequence 起点: {result['media_sequence_base']}")
    console(f"加密: {result['encryption_method']} | EXT-X-MAP: {'是' if result['has_map'] else '否'}")
    console(f"首 Key: {'OK' if result['first_key_ok'] else '-'} | "
            f"首分片媒体: {result['first_segment_media_type'] or '失败'}")
    for w in result.get("warnings", []):
        console(f"警告: {w}")
    raise typer.Exit(code=0 if result["first_segment_media_type"] else 1)


@app.command()
def download(
    url: str = _SOURCE_KWARGS["url"],
    capture_id: str = _SOURCE_KWARGS["capture_id"],
    course_url: str = _SOURCE_KWARGS["course_url"],
    har: str = _SOURCE_KWARGS["har"],
    request_index: int = _SOURCE_KWARGS["request_index"],
    profile: str = _SOURCE_KWARGS["profile"],
    headers_file: str = _SOURCE_KWARGS["headers_file"],
    cookie_env: str = _SOURCE_KWARGS["cookie_env"],
    engine: str = typer.Option("python-managed", "--engine",
                               help="ffmpeg-direct | python-managed"),
    output: str = typer.Option("output.mp4", "--output", "-o"),
    quality: str = typer.Option("best", "--quality"),
    workers: int = typer.Option(config.DEFAULT_WORKERS, "--workers"),
    retries: int = typer.Option(config.DEFAULT_RETRIES, "--retries"),
    timeout: int = typer.Option(config.DEFAULT_TIMEOUT_SECONDS, "--timeout"),
    resume: bool = typer.Option(True, "--resume/--no-resume"),
    probe_segments: int = typer.Option(None, "--probe-segments", help="只下载前 N 个分片"),
    iv_strategy: str = typer.Option("hls-spec", "--iv-strategy"),
    refresh_on_expired: str = typer.Option("none", "--refresh-on-expired",
                                           help="none | once"),
    keep_temp: bool = typer.Option(False, "--keep-temp"),
    report: str = typer.Option(None, "--report", help="report.json 输出路径"),
    authorized_content: bool = typer.Option(False, "--authorized-content"),
) -> None:
    """下载已授权 HLS 视频(双引擎)。"""
    _check_authorized(authorized_content)
    if engine not in ("ffmpeg-direct", "python-managed"):
        _fail(PocError(ErrorCode.INPUT_INVALID, f"未知引擎: {engine}"))
    if refresh_on_expired not in ("none", "once"):
        _fail(PocError(ErrorCode.INPUT_INVALID, "--refresh-on-expired 仅支持 none|once"))
    if iv_strategy == "xiaoe-legacy-index-tail":
        console("警告: 使用非标准 IV 兼容策略 xiaoe-legacy-index-tail,将在报告中记录。")

    from .engines.ffmpeg_direct import run_ffmpeg_direct
    from .engines.python_managed import run_python_managed
    from .reporting.report import write_report

    try:
        ctx = _resolve_ctx(url, capture_id, course_url, har, request_index,
                           profile, headers_file, cookie_env)
        output_path = resolve_output_path(output)
        console(f"engine: {engine} | 来源: {ctx.source_mode} | {redact_url(ctx.url_secret)}")

        request = models.DownloadRequest(
            source_mode=ctx.source_mode,
            course_url=ctx.course_url,
            capture_id=ctx.capture_id,
            output_path=str(output_path),
            quality=quality, engine=engine, workers=workers, retries=retries,
            timeout_seconds=timeout, resume=resume, iv_strategy=iv_strategy,
            refresh_on_expired=refresh_on_expired, authorized_content=True,
            probe_segments=probe_segments, keep_temp=keep_temp,
        )

        done_count = 0
        fail_count = 0

        def progress(task, exc=None):
            nonlocal done_count, fail_count
            if exc is None:
                done_count += 1
            else:
                fail_count += 1
            if (done_count + fail_count) % 20 == 0:
                console(f"  进度: 完成 {done_count},失败 {fail_count}")

        if engine == "ffmpeg-direct":
            rep = run_ffmpeg_direct(ctx, output_path, timeout=max(timeout, 3600))
        else:
            rep = run_python_managed(ctx, request, progress_cb=progress)
            console(f"分片: 成功 {rep.segment_success}/{rep.segment_total},"
                    f"失败 {rep.segment_failed}")

        if iv_strategy != "hls-spec":
            rep.warnings.append(f"使用非标准 IV 策略: {iv_strategy}")
        report_path = Path(report) if report else Path(
            str(output_path) + ".report.json")
        write_report(rep, report_path)
        console(f"输出: {output_path} ({rep.output_size} 字节)")
        console(f"时长: {rep.output_duration}s | 校验: {rep.validation_status}")
        for w in rep.warnings:
            console(f"警告: {w}")
        console(f"报告: {report_path}")
        console(f"job-id: {rep.job_id}(清理: xiaoe-hls-poc clean {rep.job_id})")
        if rep.validation_status not in ("PASSED", "DURATION_MISMATCH"):
            raise typer.Exit(code=1)
    except PocError as exc:
        _fail(exc)


# ---------------------------------------------------------------- verify / clean


@app.command()
def verify(
    output_file: str = typer.Argument(..., help="已下载的 mp4"),
    expected_duration: float = typer.Option(None, "--expected-duration"),
    skip_decode: bool = typer.Option(False, "--skip-decode"),
) -> None:
    """验证输出:ffprobe、时长一致性、首尾解码抽样,生成 metadata/sha256。"""
    from .media.ffprobe import decode_sample, duration_within_tolerance, probe_file

    path = Path(output_file).expanduser().resolve()
    if not path.is_file():
        _fail(PocError(ErrorCode.INPUT_INVALID, f"文件不存在: {path}"))
    try:
        pr = probe_file(path)
    except PocError as exc:
        _fail(exc)

    status = "PASSED"
    problems: list[str] = []
    if not pr.has_video:
        status = "FAILED"
        problems.append("OUTPUT_NO_VIDEO: 未发现视频流")
    console(f"时长: {pr.duration}s | 大小: {pr.size} | 流数: {pr.stream_count}")
    console(f"  视频: {pr.video_codec} {pr.width}x{pr.height} | 音频: {pr.audio_codec}")

    if expected_duration is not None:
        if duration_within_tolerance(pr.duration, expected_duration):
            console(f"时长一致性: OK(预期 {expected_duration:.1f}s,阈值 max(3s,1%))")
        else:
            status = "FAILED"
            problems.append(
                f"OUTPUT_DURATION_MISMATCH: 预期 {expected_duration:.1f}s 实际 {pr.duration}s"
            )

    if not skip_decode and pr.duration:
        head_ok = decode_sample(path, seek=0.0)
        tail_ok = decode_sample(path, seek=max(0.0, pr.duration - 6.0))
        console(f"首 5s 解码: {'OK' if head_ok else 'FAIL'} | "
                f"尾 5s 解码: {'OK' if tail_ok else 'FAIL'}")
        if not (head_ok and tail_ok):
            status = "FAILED"
            problems.append("解码抽样存在严重错误")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sha_path = Path(str(path) + ".sha256")
    sha_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    meta = {
        "file": path.name, "duration": pr.duration, "size": pr.size,
        "video_codec": pr.video_codec, "audio_codec": pr.audio_codec,
        "width": pr.width, "height": pr.height, "stream_count": pr.stream_count,
        "sha256": digest, "validation_status": status,
    }
    meta_path = Path(str(path) + ".metadata.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    console(f"校验结果: {status}")
    for p in problems:
        console(f"  {p}")
    console(f"已生成: {meta_path.name}, {sha_path.name}")
    raise typer.Exit(code=0 if status == "PASSED" else 1)


@app.command()
def clean(job_id: str = typer.Argument(..., help="要清理的 job-id")) -> None:
    """清理任务临时目录(明文分片、状态文件等)。"""
    if not job_id.isalnum():
        _fail(PocError(ErrorCode.INPUT_INVALID, "非法 job-id"))
    target = config.jobs_dir() / job_id
    if not target.is_dir():
        console(f"无此任务目录: {target}")
        raise typer.Exit(code=0)
    shutil.rmtree(target, ignore_errors=True)
    console(f"已清理: {target}")


if __name__ == "__main__":
    app()

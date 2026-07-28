"""Playwright 浏览器交互式登录与捕获(10.3 / 10.4)。

登录由用户本人在可见浏览器中完成;工具不模拟账号密码、不识别验证码。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from .. import models
from ..config import ensure_runtime_dirs
from ..errors import ErrorCode, PocError
from ..security.redactor import redact_url
from . import profile_store, session_store


def wait_for_user(
    profile_name: str,
    *,
    input_fn=input,
    output_fn=print,
) -> str:
    """等待用户确认:优先终端回车,stdin 不可用时轮询哨兵文件。

    后台/服务化运行时 sys.stdin 可能丢失,此时在 Profile 目录创建
    `.poc-continue` 文件即视为确认,文件内容作为输入文本返回。
    """
    import time

    sentinel = profile_store.profile_dir(profile_name) / ".poc-continue"
    sentinel.unlink(missing_ok=True)
    try:
        input_fn()
        return ""
    except (RuntimeError, EOFError, OSError):
        pass
    output_fn(f"终端输入不可用。完成操作后创建文件即继续: {sentinel}")
    while not sentinel.is_file():
        time.sleep(2)
    content = sentinel.read_text(encoding="utf-8", errors="ignore").strip()
    sentinel.unlink(missing_ok=True)
    return content


def _launch(profile_name: str):
    """启动持久化可见浏览器。返回 (playwright, context, page)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PocError(
            ErrorCode.BROWSER_NOT_INSTALLED, "Playwright 未安装"
        ) from exc

    ensure_runtime_dirs()
    p = sync_playwright().start()
    try:
        import os

        args: list[str] = []
        if cdp_port := os.environ.get("XIAOE_CDP_PORT"):
            args.append(f"--remote-debugging-port={cdp_port}")
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_store.profile_dir(profile_name)),
            headless=False,  # 10.1:强制可见
            viewport={"width": 1440, "height": 900},
            args=args,
        )
    except Exception as exc:  # noqa: BLE001
        p.stop()
        msg = str(exc)
        if "Executable doesn't exist" in msg or "browser has not been installed" in msg.lower():
            raise PocError(
                ErrorCode.BROWSER_NOT_INSTALLED,
                "Playwright Chromium 未安装",
                hint="执行: python -m playwright install chromium",
            ) from exc
        if "ProcessSingleton" in msg or "lock" in msg.lower() or "user data directory is already in use" in msg.lower():
            raise PocError(
                ErrorCode.BROWSER_PROFILE_LOCKED,
                f"Profile '{profile_name}' 被其他浏览器进程占用",
            ) from exc
        raise PocError(ErrorCode.INTERNAL_ERROR, f"浏览器启动失败: {msg[:200]}") from exc
    page = context.pages[0] if context.pages else context.new_page()
    return p, context, page


def interactive_login(
    course_url: str,
    profile_name: str,
    *,
    timeout_seconds: int = 600,
    input_fn=input,
    output_fn=print,
) -> models.AuthContext:
    """打开可见浏览器,等待用户完成登录并确认(10.3)。"""
    lock = profile_store.acquire_profile_lock(profile_name)
    try:
        p, context, page = _launch(profile_name)
        try:
            page.goto(course_url, wait_until="domcontentloaded")
            output_fn("浏览器已打开。请本人完成登录(扫码/验证码/账号均可)。")
            output_fn("确认课程页可以正常访问后,回到终端按回车继续…")
            wait_for_user(profile_name, input_fn=input_fn, output_fn=output_fn)

            state = context.storage_state()
            session_store.save_storage_state(profile_name, state)
            session_store.save_session_meta(
                profile_name, course_page_url=course_url,
                login_status=models.LOGIN_SESSION_VALID,
            )
            profile_store.save_profile_meta(
                models.BrowserProfile(
                    profile_name=profile_name,
                    user_data_dir=str(profile_store.profile_dir(profile_name)),
                )
            )
            return models.AuthContext(
                auth_context_id=uuid.uuid4().hex[:12],
                profile_name=profile_name,
                course_page_url=course_url,
                storage_state_path=str(session_store.storage_state_path(profile_name)),
                user_agent=page.evaluate("navigator.userAgent") or "",
                cookie_jar_ref=session_store.storage_state_path(profile_name).name,
                login_status=models.LOGIN_SESSION_VALID,
            )
        finally:
            context.close()
            p.stop()
    finally:
        profile_store.release_profile_lock(profile_name)


def capture_media_requests(
    course_url: str,
    profile_name: str,
    *,
    input_fn=input,
    output_fn=print,
):
    """监听 request/response,捕获 M3U8 候选(10.4)。返回原始候选列表。"""
    from ..capture.candidate_detector import RawCandidate, assess_response

    candidates: list[RawCandidate] = []
    lock = profile_store.acquire_profile_lock(profile_name)
    try:
        p, context, page = _launch(profile_name)
        try:
            def on_response(response) -> None:
                try:
                    cand = assess_response(response, page_url=page.url)
                    if cand is not None:
                        candidates.append(cand)
                except Exception:  # noqa: BLE001 - 捕获异常不中断浏览器(10.4)
                    pass

            page.on("response", on_response)
            try:
                page.goto(course_url, wait_until="domcontentloaded")
            except Exception:  # noqa: BLE001
                pass
            output_fn("监听已启动。请在浏览器中播放本人有权访问的视频。")
            output_fn("捕获完成后按回车结束…")
            wait_for_user(profile_name, input_fn=input_fn, output_fn=output_fn)

            # 登录态可能刷新,持久化 storage state
            state = context.storage_state()
            session_store.save_storage_state(profile_name, state)
            session_store.save_session_meta(
                profile_name, course_page_url=course_url,
                login_status=models.LOGIN_SESSION_VALID,
            )
            return candidates
        finally:
            context.close()
            p.stop()
    finally:
        profile_store.release_profile_lock(profile_name)


def build_auth_context(profile_name: str, course_url: str = "") -> models.AuthContext:
    """从已保存的会话构造 AuthContext(不含明文 Cookie)。"""
    meta = session_store.load_session_meta(profile_name) or {}
    status = meta.get("login_status", "UNKNOWN")
    return models.AuthContext(
        auth_context_id=uuid.uuid4().hex[:12],
        profile_name=profile_name,
        course_page_url=course_url or meta.get("course_domain", ""),
        captured_at=datetime.now(),
        storage_state_path=str(session_store.storage_state_path(profile_name))
        if session_store.storage_state_path(profile_name).is_file() else None,
        cookie_jar_ref="storage-state.json",
        login_status=status,
    )

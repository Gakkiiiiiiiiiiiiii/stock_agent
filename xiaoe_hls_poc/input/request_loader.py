"""下载请求来源解析(7 / 8.7):course-url / capture-id / url / har 四选一,显式优先。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..auth import provider
from ..capture import capture_store
from ..errors import ErrorCode, PocError
from ..security.url_policy import validate_url
from .header_loader import load_headers_file


@dataclass
class EffectiveContext:
    """统一的下载授权上下文(仅内存;url_secret/authorization 不落盘、不进日志)。"""

    source_mode: str  # course_url / capture / manual / har
    url_secret: str = field(default="", repr=False)
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[dict] = field(default_factory=list)
    authorization: str | None = field(default=None, repr=False)
    authorized_host: str | None = None
    capture_id: str | None = None
    course_url: str | None = None
    page_url: str = ""
    profile_name: str = "default"


def _parse_cookie_env(env_name: str) -> list[dict]:
    """把环境变量里的 'a=1; b=2' 转为 cookie dict 列表(仅手工模式)。"""
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        raise PocError(
            ErrorCode.INPUT_INVALID,
            f"环境变量 {env_name} 为空",
            hint="设置 Cookie 字符串,如: export XIAOE_COOKIE='a=1; b=2'",
        )
    cookies = []
    for pair in raw.split(";"):
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        cookies.append({"name": name.strip(), "value": value.strip(),
                        "domain": "", "path": "/"})
    return cookies


def resolve_effective_context(
    *,
    url: str | None = None,
    capture_id: str | None = None,
    course_url: str | None = None,
    profile: str = "default",
    headers_file: str | None = None,
    cookie_env: str | None = None,
    har: str | None = None,
    request_index: int | None = None,
) -> EffectiveContext:
    sources = [s for s in (url, capture_id, course_url, har) if s]
    if len(sources) != 1:
        raise PocError(
            ErrorCode.INPUT_INVALID,
            "输入来源必须且只能四选一: --url / --capture-id / --course-url / --har",
        )

    headers: dict[str, str] = {}
    authorization = None
    if headers_file:
        headers, authorization = load_headers_file(headers_file)

    if url:
        validate_url(url)
        return EffectiveContext(
            source_mode="manual",
            url_secret=url,
            headers=headers,
            cookies=_parse_cookie_env(cookie_env) if cookie_env else [],
            authorization=authorization,
            profile_name=profile,
        )

    if har:
        from .har_loader import load_har

        entries = load_har(har)
        if not entries:
            raise PocError(ErrorCode.CAPTURE_NO_MEDIA_REQUEST, "HAR 中未发现 M3U8 请求")
        if request_index is not None:
            entry = next((e for e in entries if e.index == request_index), None)
            if entry is None:
                raise PocError(
                    ErrorCode.INPUT_INVALID, f"HAR 中不存在 request-index {request_index}"
                )
        else:
            ok = [e for e in entries if e.status in (200, 206)]
            entry = (ok or entries)[0]
        validate_url(entry.url)
        return EffectiveContext(
            source_mode="har",
            url_secret=entry.url,
            headers={**entry.headers, **headers},
            cookies=_parse_cookie_env(cookie_env) if cookie_env else [],
            authorization=authorization,
            profile_name=profile,
        )

    if capture_id:
        capture = capture_store.load_capture(capture_id)
        real_url = capture_store.resolve_capture_url(capture)
        validate_url(real_url)
        cookies, _ = provider.get_download_material(profile)
        return EffectiveContext(
            source_mode="capture",
            url_secret=real_url,
            headers=headers,
            cookies=cookies,
            authorization=authorization,
            capture_id=capture_id,
            course_url=capture.page_url or None,
            page_url=capture.page_url,
            profile_name=profile,
        )

    # course_url:需要先用浏览器重新捕获当前有效播放地址(10.1)
    assert course_url is not None
    validate_url(course_url)
    from ..capture.network_capture import run_capture

    cap = run_capture(course_url, profile)
    return resolve_effective_context(capture_id=cap.capture_id, profile=profile)

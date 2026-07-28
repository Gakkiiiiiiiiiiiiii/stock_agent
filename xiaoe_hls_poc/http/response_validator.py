"""响应校验(13.3):状态码、错误页、大小上限。"""

from __future__ import annotations

import httpx

from ..errors import ErrorCode, PocError
from ..media.segment_validator import looks_like_error_page
from ..security.redactor import redact_url


def raise_for_classified_status(resp: httpx.Response, *, kind: str = "RESOURCE") -> None:
    status = resp.status_code
    if status < 400:
        return
    url = redact_url(str(resp.url))
    if status == 401:
        code = ErrorCode.PLAYLIST_HTTP_401 if kind == "PLAYLIST" else ErrorCode.SEGMENT_HTTP_ERROR
        raise PocError(code, f"{kind} 返回 401 未授权: {url}")
    if status == 403:
        code = ErrorCode.PLAYLIST_HTTP_403 if kind == "PLAYLIST" else ErrorCode.SEGMENT_HTTP_ERROR
        raise PocError(code, f"{kind} 返回 403(签名过期或 Header 缺失): {url}")
    raise PocError(
        ErrorCode.SEGMENT_HTTP_ERROR if kind != "KEY" else ErrorCode.KEY_HTTP_ERROR,
        f"{kind} HTTP {status}: {url}",
    )


def ensure_not_error_page(data: bytes, *, kind: str = "RESOURCE") -> None:
    if looks_like_error_page(data):
        raise PocError(
            ErrorCode.PLAYLIST_INVALID if kind == "PLAYLIST" else ErrorCode.DECRYPT_MEDIA_INVALID,
            f"{kind} 响应疑似 HTML/JSON 错误页",
        )


def ensure_size_within(size: int, limit: int, *, kind: str = "RESOURCE") -> None:
    if size > limit:
        raise PocError(
            ErrorCode.INPUT_INVALID, f"{kind} 大小 {size} 超过限制 {limit}"
        )

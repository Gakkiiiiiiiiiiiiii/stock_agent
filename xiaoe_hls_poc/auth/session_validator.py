"""会话状态分类(10.8):区分登录失效 / URL 过期 / 上下文不完整。"""

from __future__ import annotations

from .. import models
from ..errors import ErrorCode, PocError


def classify_http_error(exc: PocError, *, browser_can_play: bool | None = None) -> str:
    """根据下载侧错误与浏览器侧表现分类(10.8)。"""
    if exc.code == ErrorCode.PLAYLIST_HTTP_401:
        return models.LOGIN_SESSION_EXPIRED
    if exc.code == ErrorCode.PLAYLIST_HTTP_403:
        # 登录仍有效但旧签名 URL 过期是最常见情形;若浏览器内同 URL 也失败,
        # 说明权益/资源变化,不继续猜测
        if browser_can_play is True:
            return models.AUTH_CONTEXT_INCOMPLETE
        return models.PLAYLIST_URL_EXPIRED
    if exc.code in (ErrorCode.LOGIN_REQUIRED, ErrorCode.LOGIN_SESSION_EXPIRED):
        return models.LOGIN_SESSION_EXPIRED
    return "UNKNOWN"


def classify_session_status(
    *,
    profile_exists: bool,
    has_storage_state: bool,
    cookie_count: int,
) -> tuple[str, bool]:
    """session-status 的粗粒度判断。返回 (状态, 是否需要重新登录)。"""
    if not profile_exists or not has_storage_state:
        return models.LOGIN_SESSION_EXPIRED, True
    if cookie_count == 0:
        return models.LOGIN_SESSION_EXPIRED, True
    return models.LOGIN_SESSION_VALID, False

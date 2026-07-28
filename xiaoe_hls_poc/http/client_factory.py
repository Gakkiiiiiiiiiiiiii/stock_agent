"""HTTP 客户端构造(13.5):共享不可变 Header 策略 + 只读 Cookie 快照。"""

from __future__ import annotations

import httpx

from ..auth.cookie_adapter import build_httpx_cookies
from ..config import MAX_REDIRECTS
from .auth_header_policy import apply_authorization


def build_client(
    *,
    headers: dict[str, str] | None = None,
    cookies: list[dict] | None = None,
    authorization: str | None = None,
    authorized_host: str | None = None,
    target_url: str | None = None,
    timeout: float = 30.0,
) -> httpx.Client:
    hdrs = dict(headers or {})
    if authorization and target_url:
        hdrs = apply_authorization(hdrs, authorization, target_url, authorized_host)
    return httpx.Client(
        headers=hdrs,
        cookies=build_httpx_cookies(cookies or []),
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        verify=True,
        # 不读取系统/环境代理:避免本机回环地址被代理拦截(502),
        # 也保证下载链路与浏览器登录链路网络行为可预期、可诊断
        trust_env=False,
    )

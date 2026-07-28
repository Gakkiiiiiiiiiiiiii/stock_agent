"""Cookie Adapter(10.6):按 domain/path/secure 规则注入 HTTP Cookie Jar。"""

from __future__ import annotations

import time
from urllib.parse import urlsplit


def cookie_matches_url(cookie: dict, url: str, *, now: float | None = None) -> bool:
    """判断浏览器导出的 Cookie 是否应发送到 url(domain/path/secure/expires)。"""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    scheme = parts.scheme.lower()

    domain = (cookie.get("domain") or "").lower()
    host_only = not domain.startswith(".")
    domain = domain.lstrip(".")
    if host_only:
        if host != domain:
            return False
    else:
        if host != domain and not host.endswith("." + domain):
            return False

    cookie_path = cookie.get("path") or "/"
    if not path.startswith(cookie_path):
        return False

    if cookie.get("secure") and scheme != "https":
        return False

    expires = cookie.get("expires")
    if expires and expires > 0 and expires <= (now if now is not None else time.time()):
        return False

    return True


def build_httpx_cookies(cookies: list[dict]):
    """将 Playwright storage_state cookies 转为 httpx.Cookies。"""
    import httpx

    jar = httpx.Cookies()
    for c in cookies:
        jar.set(
            c["name"],
            c["value"],
            domain=c.get("domain") or "",
            path=c.get("path") or "/",
        )
    return jar


def cookie_header_for_url(cookies: list[dict], url: str) -> str:
    """为 ffmpeg -cookies 参数/手工请求构造匹配的 Cookie 字符串。"""
    pairs = [f"{c['name']}={c['value']}" for c in cookies if cookie_matches_url(c, url)]
    return "; ".join(pairs)


def filter_cookies_for_url(cookies: list[dict], url: str) -> list[dict]:
    return [c for c in cookies if cookie_matches_url(c, url)]

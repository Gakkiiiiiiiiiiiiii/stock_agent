"""输入 URL 校验(11.1 / 22.5)。生产要求 HTTPS;回环地址允许 HTTP 供本地测试。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from ..errors import ErrorCode, PocError

ALLOWED_SCHEMES = ("https", "http")


def is_loopback_host(host: str) -> bool:
    host = host.strip().lower()
    if host in ("localhost", "localhost."):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback
    except ValueError:
        pass
    # 尝试解析域名
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_loopback:
                return False
        return True
    except (OSError, ValueError):
        return False


def validate_url(url: str, *, allow_http_loopback: bool = True) -> str:
    """校验 URL,返回原 URL。失败抛 PocError(INPUT_INVALID)。"""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise PocError(ErrorCode.INPUT_INVALID, f"URL 无法解析: {exc}") from exc
    if parts.scheme not in ALLOWED_SCHEMES:
        raise PocError(
            ErrorCode.INPUT_INVALID,
            f"不支持的协议: {parts.scheme or '(空)'}",
            hint="仅支持 HTTPS;本地回环地址允许 HTTP",
        )
    if not parts.hostname:
        raise PocError(ErrorCode.INPUT_INVALID, "URL 缺少主机名")
    if parts.scheme == "http":
        if not (allow_http_loopback and is_loopback_host(parts.hostname)):
            raise PocError(
                ErrorCode.INPUT_INVALID,
                "非回环地址必须使用 HTTPS",
                hint="本地测试请使用 127.0.0.1/localhost",
            )
    return url


def validate_redirect_target(url: str) -> str:
    """重定向后重新校验(11.1)。"""
    return validate_url(url)

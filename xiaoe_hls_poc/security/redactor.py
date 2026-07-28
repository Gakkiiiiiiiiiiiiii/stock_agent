"""日志与报告脱敏(设计文档 21.2)。所有对外输出必须经过本模块。"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 设计文档 21.2 指定必须脱敏的查询参数
SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "sign",
        "token",
        "auth_key",
        "authkey",
        "expires",
        "expire",
        "session",
        "sessionid",
        "session_id",
        "ticket",
        "credential",
        "signature",
        "access_token",
        "auth",
    }
)

SENSITIVE_HEADERS = frozenset(
    {
        "cookie",
        "set-cookie",
        "authorization",
        "proxy-authorization",
        "x-auth-token",
        "x-access-token",
        "x-api-key",
    }
)

_HEADER_LINE_RE = re.compile(
    r"(?i)\b(cookie|set-cookie|authorization|proxy-authorization|x-auth-token|"
    r"x-access-token|x-api-key)\s*[:=]\s*[^\s,;]+(?:\s*;\s*[^\s,;]+=[^,;]*)*"
)


def redact_url(url: str) -> str:
    """把敏感查询参数值替换为 ***(字面量,不再 URL 编码)。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    segments = []
    for k, v in pairs:
        if k.lower() in SENSITIVE_QUERY_PARAMS:
            segments.append(f"{k}=***")
        else:
            segments.append(urlencode([(k, v)]))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "&".join(segments), parts.fragment)
    )


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        out[k] = "***" if k.lower() in SENSITIVE_HEADERS else v
    return out


def redact_text(text: str) -> str:
    """对任意文本(stderr、异常消息)中的敏感 Header 片段脱敏。"""
    return _HEADER_LINE_RE.sub(lambda m: f"{m.group(1)}: ***", text)


def url_fingerprint(url: str) -> str:
    """脱敏后 URL 的 sha256,用于 state.json / 缓存键。"""
    return hashlib.sha256(redact_url(url).encode("utf-8")).hexdigest()


def path_fingerprint(url: str) -> str:
    """scheme+host+path 的哈希(忽略查询参数),用于判断“同一路径不同签名”。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return hashlib.sha256(b"<invalid-url>").hexdigest()
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def safe_query_fingerprint(url: str) -> str:
    """sha256(scheme+host+path+非敏感查询字段),用于 Key 缓存键(14.4)。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return hashlib.sha256(b"<invalid-url>").hexdigest()
    pairs = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in SENSITIVE_QUERY_PARAMS
    )
    base = f"{parts.scheme}://{parts.netloc}{parts.path}?{urlencode(pairs)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

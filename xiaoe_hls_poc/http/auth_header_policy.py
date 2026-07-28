"""Header 继承策略(13.2):allowlist、跨域 Authorization 移除。"""

from __future__ import annotations

from urllib.parse import urlsplit

# 允许复用(10.6)
ALLOWED_HEADERS = frozenset(
    {"user-agent", "referer", "origin", "accept", "accept-language"}
)

# 由客户端重建,绝不复制
REBUILT_HEADERS = frozenset({"cookie", "host", "content-length", "connection"})


def filter_reusable_headers(headers: dict[str, str]) -> dict[str, str]:
    """只保留 allowlist 中的 Header(Authorization 单独处理)。"""
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in ALLOWED_HEADERS:
            out[k] = v
    return out


def _registrable_domain(host: str) -> str:
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def apply_authorization(
    headers: dict[str, str],
    authorization: str | None,
    target_url: str,
    authorized_host: str | None,
) -> dict[str, str]:
    """Authorization 仅发送到捕获时使用该 Header 的受信域名(13.2);
    跨域(不同 registrable domain)时移除。"""
    out = dict(headers)
    if not authorization or not authorized_host:
        out.pop("Authorization", None)
        out.pop("authorization", None)
        return out
    target_host = urlsplit(target_url).hostname or ""
    if _registrable_domain(target_host) == _registrable_domain(authorized_host):
        out["Authorization"] = authorization
    else:
        out.pop("Authorization", None)
        out.pop("authorization", None)
    return out

"""HAR 导入(7.4):全程本地处理,只筛 M3U8 候选,不保存副本。"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ErrorCode, PocError
from ..security.redactor import redact_headers, redact_url


@dataclass
class HarM3u8Entry:
    index: int
    url: str
    status: int
    content_type: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)  # 可复用 Header
    has_authorization: bool = False
    body_starts_with_extm3u: bool = False

    def redacted_row(self) -> dict:
        return {
            "index": self.index,
            "url": redact_url(self.url),
            "status": self.status,
            "content_type": self.content_type,
            "method": self.method,
            "headers": redact_headers(self.headers),
            "has_authorization": self.has_authorization,
            "body_extm3u": self.body_starts_with_extm3u,
        }


def _looks_like_m3u8(url: str, mime: str, body_head: str) -> bool:
    return (
        ".m3u8" in url.lower()
        or "mpegurl" in mime.lower()
        or body_head.lstrip().startswith("#EXTM3U")
    )


def load_har(path: str | Path) -> list[HarM3u8Entry]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise PocError(ErrorCode.INPUT_INVALID, f"HAR 文件不存在: {p.name}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise PocError(ErrorCode.INPUT_INVALID, "HAR 不是合法 JSON") from exc

    entries = raw.get("log", {}).get("entries", [])
    out: list[HarM3u8Entry] = []
    for i, entry in enumerate(entries):
        req = entry.get("request", {})
        resp = entry.get("response", {})
        url = req.get("url", "")
        mime = resp.get("content", {}).get("mimeType", "") or ""
        body_head = ""
        text = resp.get("content", {}).get("text")
        if text:
            if resp.get("content", {}).get("encoding") == "base64":
                try:
                    body_head = base64.b64decode(text[:256]).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    body_head = ""
            else:
                body_head = text[:256]
        if not _looks_like_m3u8(url, mime, body_head):
            continue
        headers: dict[str, str] = {}
        has_auth = False
        for h in req.get("headers", []):
            name = (h.get("name") or "").lower()
            if name == "authorization":
                has_auth = True
            if name in ("user-agent", "referer", "origin", "accept", "accept-language"):
                headers[h["name"]] = h.get("value", "")
        out.append(
            HarM3u8Entry(
                index=i,
                url=url,
                status=resp.get("status", 0),
                content_type=mime,
                method=req.get("method", "GET"),
                headers=headers,
                has_authorization=has_auth,
                body_starts_with_extm3u=body_head.lstrip().startswith("#EXTM3U"),
            )
        )
    return out

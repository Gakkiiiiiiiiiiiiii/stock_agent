"""Header 文件加载(7.5):仅 allowlist Header 入库,敏感 Header 单独返回。"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import ErrorCode, PocError
from ..http.auth_header_policy import filter_reusable_headers


def load_headers_file(path: str | Path) -> tuple[dict[str, str], str | None]:
    """返回 (可复用 headers, authorization)。authorization 不写入任何文件。"""
    p = Path(path).expanduser()
    if not p.is_file():
        raise PocError(ErrorCode.INPUT_INVALID, f"Header 文件不存在: {p.name}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PocError(ErrorCode.INPUT_INVALID, "Header 文件不是合法 JSON") from exc
    if not isinstance(raw, dict):
        raise PocError(ErrorCode.INPUT_INVALID, "Header 文件必须是 JSON 对象")

    authorization = None
    cleaned: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(v, str):
            continue
        if k.lower() == "authorization":
            authorization = v
            continue
        cleaned[k] = v
    return filter_reusable_headers(cleaned), authorization

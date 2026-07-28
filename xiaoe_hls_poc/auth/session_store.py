"""会话元数据与 storage state 存储(10.7)。session-meta 不含 Cookie 值。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..config import _chmod_user_only, auth_dir, protect_file
from ..errors import ErrorCode, PocError
from ..security.path_policy import sanitize_filename


def session_dir(profile_name: str) -> Path:
    return auth_dir() / sanitize_filename(profile_name)


def storage_state_path(profile_name: str) -> Path:
    return session_dir(profile_name) / "storage-state.json"


def session_meta_path(profile_name: str) -> Path:
    return session_dir(profile_name) / "session-meta.json"


def save_storage_state(profile_name: str, state: dict) -> Path:
    d = session_dir(profile_name)
    d.mkdir(parents=True, exist_ok=True)
    _chmod_user_only(d)
    p = storage_state_path(profile_name)
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    protect_file(p)
    return p


def load_storage_state(profile_name: str) -> dict | None:
    p = storage_state_path(profile_name)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PocError(ErrorCode.INTERNAL_ERROR, "storage-state.json 损坏") from exc


def load_cookies(profile_name: str) -> list[dict]:
    state = load_storage_state(profile_name)
    if not state:
        return []
    return state.get("cookies", [])


def save_session_meta(
    profile_name: str,
    *,
    course_page_url: str = "",
    login_status: str = "UNKNOWN",
) -> Path:
    """只保存元数据:时间、域名、Cookie 数量统计,不保存任何 Cookie 值。"""
    from urllib.parse import urlsplit

    cookies = load_cookies(profile_name)
    domains = sorted({(c.get("domain") or "").lstrip(".") for c in cookies} - {""})
    meta = {
        "profile_name": profile_name,
        "last_auth_at": datetime.now().isoformat(),
        "course_domain": urlsplit(course_page_url).hostname or "",
        "cookie_count": len(cookies),
        "cookie_domains": domains,
        "login_status": login_status,
    }
    d = session_dir(profile_name)
    d.mkdir(parents=True, exist_ok=True)
    p = session_meta_path(profile_name)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    protect_file(p)
    return p


def load_session_meta(profile_name: str) -> dict | None:
    p = session_meta_path(profile_name)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def clear_session(profile_name: str) -> bool:
    import shutil

    d = session_dir(profile_name)
    if not d.is_dir():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True

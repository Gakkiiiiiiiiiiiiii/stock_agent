"""Auth Provider(6.1):统一对外提供授权上下文构建入口。"""

from __future__ import annotations

from ..models import AuthContext
from . import browser_auth, session_store


def get_auth_context(profile_name: str, course_url: str = "") -> AuthContext:
    return browser_auth.build_auth_context(profile_name, course_url)


def get_download_material(profile_name: str) -> tuple[list[dict], str | None]:
    """返回 (cookies, storage_state_path)。Cookie 只在内存与受保护文件中存在。"""
    cookies = session_store.load_cookies(profile_name)
    path = session_store.storage_state_path(profile_name)
    return cookies, str(path) if path.is_file() else None

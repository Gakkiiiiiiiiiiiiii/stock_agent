"""浏览器 Profile 存储(10.2 / 10.7):独立持久化目录,不读系统 Chrome。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..config import _chmod_user_only, profiles_dir, protect_file
from ..errors import ErrorCode, PocError
from ..models import BrowserProfile
from ..security.path_policy import sanitize_filename


def profile_dir(profile_name: str) -> Path:
    name = sanitize_filename(profile_name)
    return profiles_dir() / name


def meta_path(profile_name: str) -> Path:
    return profile_dir(profile_name) / "profile-meta.json"


def lock_path(profile_name: str) -> Path:
    return profile_dir(profile_name) / ".poc-lock"


def profile_exists(profile_name: str) -> bool:
    return profile_dir(profile_name).is_dir()


def save_profile_meta(profile: BrowserProfile) -> None:
    d = profile_dir(profile.profile_name)
    d.mkdir(parents=True, exist_ok=True)
    _chmod_user_only(d)
    profile.last_used_at = datetime.now()
    p = meta_path(profile.profile_name)
    p.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    protect_file(p)


def load_profile_meta(profile_name: str) -> BrowserProfile | None:
    p = meta_path(profile_name)
    if not p.is_file():
        if profile_exists(profile_name):
            return BrowserProfile(
                profile_name=profile_name, user_data_dir=str(profile_dir(profile_name))
            )
        return None
    try:
        return BrowserProfile(**json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PocError(ErrorCode.INTERNAL_ERROR, "profile-meta.json 损坏") from exc


def acquire_profile_lock(profile_name: str) -> Path:
    """简陋的 Profile 锁:存在锁文件则拒绝并发使用(10.7/22.2)。"""
    d = profile_dir(profile_name)
    d.mkdir(parents=True, exist_ok=True)
    lock = lock_path(profile_name)
    try:
        fd = lock.open("x")
    except FileExistsError as exc:
        raise PocError(
            ErrorCode.BROWSER_PROFILE_LOCKED,
            f"Profile '{profile_name}' 被其他进程占用",
            hint="关闭对应浏览器后重试,或删除锁文件",
        ) from exc
    fd.write(str(__import__("os").getpid()))
    fd.close()
    return lock


def release_profile_lock(profile_name: str) -> None:
    lock_path(profile_name).unlink(missing_ok=True)


def delete_profile(profile_name: str) -> bool:
    import shutil

    d = profile_dir(profile_name)
    if not d.is_dir():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True

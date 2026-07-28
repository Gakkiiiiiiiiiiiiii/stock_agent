"""全局配置:运行期目录、资源限制、FFmpeg 定位(设计文档 10.7/22.6/24)。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .errors import ErrorCode, PocError

# ---- 资源限制(22.6) ----
MAX_PLAYLIST_SIZE = 5 * 1024 * 1024
MAX_SEGMENT_COUNT = 20_000
MAX_SINGLE_SEGMENT_SIZE = 256 * 1024 * 1024
MAX_WORKERS = 8
DEFAULT_WORKERS = 4
MAX_REDIRECTS = 5
MAX_AUTH_REFRESH = 1
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRIES = 4
MAX_FFMPEG_SECONDS = 6 * 3600


def home_dir() -> Path:
    """运行期数据根目录,默认 ~/.xiaoe-hls-poc,可用 XIAOE_HOME 覆盖。"""
    override = os.environ.get("XIAOE_HOME")
    base = Path(override) if override else Path.home() / ".xiaoe-hls-poc"
    return base


def profiles_dir() -> Path:
    return home_dir() / "profiles"


def auth_dir() -> Path:
    return home_dir() / "auth"


def captures_dir() -> Path:
    return home_dir() / "captures"


def jobs_dir() -> Path:
    return home_dir() / "jobs"


def ensure_runtime_dirs() -> None:
    for d in (home_dir(), profiles_dir(), auth_dir(), captures_dir(), jobs_dir()):
        d.mkdir(parents=True, exist_ok=True)
        _chmod_user_only(d)


def _chmod_user_only(path: Path) -> None:
    """POSIX 下设置 0700/0600;Windows 依赖当前用户 ACL(os.chmod 尽力而为)。"""
    if sys.platform == "win32":
        return
    try:
        if path.is_dir():
            path.chmod(0o700)
        else:
            path.chmod(0o600)
    except OSError:
        pass


def protect_file(path: Path) -> None:
    if sys.platform == "win32":
        return
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _search_project_tools() -> Path | None:
    """在项目 tools/ffmpeg/**/bin 下查找(向上回溯若干层)。"""
    here = Path(__file__).resolve()
    for parent in [Path.cwd(), *here.parents, *Path.cwd().parents]:
        tools = parent / "tools" / "ffmpeg"
        if tools.is_dir():
            bins = sorted(tools.glob("**/bin"))
            for b in bins:
                return b
    return None


def locate_binary(kind: str) -> Path:
    """按顺序定位 ffmpeg/ffprobe:
    环境变量 XIAOE_FFMPEG / XIAOE_FFPROBE -> PATH -> 项目 tools/ffmpeg/**/bin/。
    """
    assert kind in ("ffmpeg", "ffprobe")
    env_name = f"XIAOE_{kind.upper()}"
    env_val = os.environ.get(env_name)
    exe = f"{kind}.exe" if sys.platform == "win32" else kind

    if env_val:
        p = Path(env_val)
        if p.is_file():
            return p
        raise PocError(
            ErrorCode.FFMPEG_NOT_FOUND,
            f"环境变量 {env_name} 指向的路径不存在",
            hint=f"修正 {env_name} 或删除该变量",
        )

    on_path = shutil.which(kind)
    if on_path:
        return Path(on_path)

    bin_dir = _search_project_tools()
    if bin_dir:
        candidate = bin_dir / exe
        if candidate.is_file():
            return candidate

    raise PocError(
        ErrorCode.FFMPEG_NOT_FOUND,
        f"未找到 {kind}",
        hint="设置 XIAOE_FFMPEG/XIAOE_FFPROBE、加入 PATH,或解压便携版到 tools/ffmpeg/",
    )


def find_binary_or_none(kind: str) -> Path | None:
    try:
        return locate_binary(kind)
    except PocError:
        return None

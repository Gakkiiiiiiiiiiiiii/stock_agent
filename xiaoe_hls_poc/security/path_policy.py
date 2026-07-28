"""输出路径清洗与穿越防护(22.4)。"""

from __future__ import annotations

import re
from pathlib import Path

from ..errors import ErrorCode, PocError

_ILLEGAL_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """清洗文件名片段,禁止路径分隔与 Windows 保留名。"""
    name = name.replace("/", "_").replace("\\", "_").strip().strip(".")
    name = _ILLEGAL_CHARS.sub("_", name)
    if not name or not name.strip("_"):
        raise PocError(ErrorCode.INPUT_INVALID, "文件名清洗后为空")
    stem = name.split(".")[0].lower()
    if stem in _RESERVED_NAMES:
        name = f"_{name}"
    return name[:200]


def resolve_output_path(path: str, *, base_dir: Path | None = None) -> Path:
    """解析输出路径:展开 ~,转绝对路径,拒绝指向系统关键目录之外不做更多限制,
    但必须是文件路径且父目录可创建。"""
    if not path or not path.strip():
        raise PocError(ErrorCode.INPUT_INVALID, "输出路径为空")
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (base_dir or Path.cwd()) / p
    p = p.resolve()
    if p.name in (".", "..") or p.name == "":
        raise PocError(ErrorCode.INPUT_INVALID, "非法输出文件名")
    return p


def ensure_within(child: Path, parent: Path) -> Path:
    """确认 child 位于 parent 之内(防路径穿越)。"""
    child_r = child.resolve()
    parent_r = parent.resolve()
    if parent_r != child_r and parent_r not in child_r.parents:
        raise PocError(ErrorCode.INPUT_INVALID, "路径越出允许目录")
    return child_r

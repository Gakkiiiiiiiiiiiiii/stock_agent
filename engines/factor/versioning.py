"""数据版本有效性判定：UNKNOWN 等占位值不算有效版本。"""
from __future__ import annotations

_UNKNOWN_VERSION_VALUES = {
    "",
    "UNKNOWN",
    "NONE",
    "NULL",
    "N/A",
    "NA",
}


def is_known_version(value: str | None) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    return text.upper() not in _UNKNOWN_VERSION_VALUES


__all__ = ["is_known_version"]

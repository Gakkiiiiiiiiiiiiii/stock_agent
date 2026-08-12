"""Shared data-version contract helpers."""

_UNKNOWN_VERSION_VALUES = {"", "UNKNOWN", "NONE", "NULL", "N/A", "NA"}


def is_known_version(value: str | None) -> bool:
    return value is not None and str(value).strip().upper() not in _UNKNOWN_VERSION_VALUES

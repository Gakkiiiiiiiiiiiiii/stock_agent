from __future__ import annotations

from pathlib import Path
from typing import Iterable


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_list(value: Iterable[float | None]) -> list[float | None]:
    return list(value)


def pct(value: float) -> float:
    return round(value * 100, 2)


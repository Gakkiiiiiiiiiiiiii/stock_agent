"""Validate that the checked-in lock manifest covers declared direct deps."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path


def check(root: Path) -> list[str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock_lines = [line.strip() for line in (root / "requirements.lock").read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    unpinned = [line for line in lock_lines if "==" not in line or line.startswith("-e ")]
    locked = {re.split(r"[<>=!~\[]", line, maxsplit=1)[0].lower() for line in lock_lines}
    required = {re.split(r"[<>=!~\[]", line, maxsplit=1)[0].lower() for line in project.get("dependencies", [])}
    return sorted([*(f"missing direct dependency: {name}" for name in required - locked), *(f"unpinned lock entry: {line}" for line in unpinned)])


if __name__ == "__main__":
    problems = check(Path(__file__).resolve().parents[1])
    print("\n".join(problems))
    raise SystemExit(1 if problems else 0)

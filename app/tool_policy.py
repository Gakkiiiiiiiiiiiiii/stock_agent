from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class PermissionLevel(str, Enum):
    READ = "READ"
    COMPUTE = "COMPUTE"
    INTERNAL_WRITE = "INTERNAL_WRITE"


@dataclass(frozen=True)
class ToolPolicy:
    permission: PermissionLevel
    timeout_seconds: float = 30.0
    output_limit_bytes: int = 64_000


class ToolAuditor:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("TOOL_AUDIT_LOG_PATH", "storage/runtime/tool_audit.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(self._redact(row), ensure_ascii=False, sort_keys=True) + "\n")

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "***REDACTED***" if any(token in str(key).lower() for token in ("key", "token", "cookie", "secret", "password")) else cls._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value

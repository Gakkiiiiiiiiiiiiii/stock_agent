from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from financial_agent.utils import project_root

AUDIT_ROOT = "storage/audit/factor_oos"


def append_oos_audit(record: dict[str, Any], root: str | Path | None = None) -> str:
    now = datetime.now(timezone.utc)
    env_root = os.getenv("FACTOR_OOS_AUDIT_ROOT")
    base = Path(root or env_root) if (root or env_root) else project_root() / AUDIT_ROOT
    month_dir = base / now.strftime("%Y%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / f"factor_oos_{now.strftime('%Y%m%d')}.jsonl"
    payload = {**record, "audit_written_at": now.isoformat(timespec="seconds")}
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n"
    lock = FileLock(str(path) + ".lock", timeout=30)
    with lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    return str(path)


__all__ = ["AUDIT_ROOT", "append_oos_audit"]

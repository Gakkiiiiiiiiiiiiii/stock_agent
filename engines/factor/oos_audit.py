from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from financial_agent.utils import project_root

AUDIT_ROOT = "storage/audit/factor_oos"
AUDIT_URI_SCHEME = "factor-oos://"


@dataclass(frozen=True)
class AuditWriteResult:
    """审计写入结果：因子库只保存可迁移的 uri，绝对路径仅用于日志。"""

    uri: str
    relative_path: str
    absolute_path: str
    record_id: str


def _audit_base(root: str | Path | None = None) -> Path:
    env_root = os.getenv("FACTOR_OOS_AUDIT_ROOT")
    base = Path(root or env_root) if (root or env_root) else project_root() / AUDIT_ROOT
    return base


def append_oos_audit(record: dict[str, Any], root: str | Path | None = None) -> AuditWriteResult:
    now = datetime.now(timezone.utc)
    base = _audit_base(root)
    month_dir = base / now.strftime("%Y%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / f"factor_oos_{now.strftime('%Y%m%d')}.jsonl"
    record_id = f"{record.get('research_run_id') or 'UNKNOWN'}:{record.get('candidate_hash') or 'UNKNOWN'}"
    payload = {
        **record,
        "audit_record_id": record_id,
        "audit_written_at": now.isoformat(timespec="seconds"),
    }
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n"
    lock = FileLock(str(path) + ".lock", timeout=30)
    with lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    relative_path = f"{now.strftime('%Y%m')}/{path.name}"
    candidate_hash = str(record.get("candidate_hash") or "")
    uri = f"{AUDIT_URI_SCHEME}{relative_path}"
    if candidate_hash:
        uri = f"{uri}#{candidate_hash}"
    return AuditWriteResult(
        uri=uri,
        relative_path=relative_path,
        absolute_path=str(path),
        record_id=record_id,
    )


def resolve_oos_audit_uri(uri: str, root: str | Path | None = None) -> tuple[Path, str | None]:
    """把可迁移 URI 解析回 (文件绝对路径, candidate_hash fragment)。"""
    text = str(uri or "")
    if not text.startswith(AUDIT_URI_SCHEME):
        raise ValueError(f"INVALID_OOS_AUDIT_URI:{text}")
    body = text[len(AUDIT_URI_SCHEME):]
    relative, _, fragment = body.partition("#")
    if not relative:
        raise ValueError(f"INVALID_OOS_AUDIT_URI:{text}")
    return _audit_base(root) / relative, (fragment or None)


def read_oos_audit(uri: str, root: str | Path | None = None) -> dict:
    """按 URI 读取审计记录；带 fragment 时按 candidate_hash 定位最后一条匹配记录。"""
    path, fragment = resolve_oos_audit_uri(uri, root=root)
    matches: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if fragment is None or str(record.get("candidate_hash") or "") == fragment:
                matches.append(record)
    if not matches:
        raise FileNotFoundError(f"OOS_AUDIT_RECORD_NOT_FOUND:{uri}")
    return matches[-1]


__all__ = [
    "AUDIT_ROOT",
    "AUDIT_URI_SCHEME",
    "AuditWriteResult",
    "append_oos_audit",
    "resolve_oos_audit_uri",
    "read_oos_audit",
]

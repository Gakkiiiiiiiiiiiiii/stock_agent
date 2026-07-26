from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock

from financial_agent.utils import project_root

AUDIT_ROOT = "storage/audit/factor_oos"
AUDIT_URI_SCHEME = "factor-oos://"


@dataclass(frozen=True)
class AuditWriteResult:
    """审计写入结果：因子库只保存可迁移、精确指向单条记录的 uri。"""

    uri: str
    relative_path: str
    absolute_path: str
    record_id: str


def build_audit_record_id(research_run_id: str, candidate_hash: str, event: str) -> str:
    """审计记录主键：Run + Candidate + 事件 + 随机后缀，同一 Candidate 多事件/多 Run 均不冲突。"""
    return f"{research_run_id}:{candidate_hash}:{event}:{uuid4().hex}"


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
    record_id = build_audit_record_id(
        str(record.get("research_run_id") or "UNKNOWN_RUN"),
        str(record.get("candidate_hash") or "UNKNOWN_CANDIDATE"),
        str(record.get("event") or "UNKNOWN_EVENT"),
    )
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
    uri = f"{AUDIT_URI_SCHEME}{relative_path}#{record_id}"
    return AuditWriteResult(
        uri=uri,
        relative_path=relative_path,
        absolute_path=str(path),
        record_id=record_id,
    )


def resolve_oos_audit_uri(uri: str, root: str | Path | None = None) -> tuple[Path, str | None]:
    """把可迁移 URI 解析回 (文件绝对路径, fragment)。fragment 为 Audit Record ID。"""
    text = str(uri or "")
    if not text.startswith(AUDIT_URI_SCHEME):
        raise ValueError(f"INVALID_OOS_AUDIT_URI:{text}")
    body = text[len(AUDIT_URI_SCHEME):]
    relative, _, fragment = body.partition("#")
    if not relative:
        raise ValueError(f"INVALID_OOS_AUDIT_URI:{text}")
    return _audit_base(root) / relative, (fragment or None)


def _is_legacy_candidate_fragment(fragment: str) -> bool:
    """旧格式 fragment 为裸 Candidate Hash（无冒号分隔的 Record ID）。"""
    return ":" not in fragment


def read_oos_audit(
    uri: str,
    root: str | Path | None = None,
    *,
    allow_legacy_candidate_fragment: bool = False,
) -> dict:
    """按 URI 精确读取单条审计记录（匹配 audit_record_id，禁止"选最后一条"）。

    旧格式（fragment 为 Candidate Hash）默认拒绝，因为它可能命中多条事件/多个 Run。
    """
    path, fragment = resolve_oos_audit_uri(uri, root=root)
    if not fragment:
        raise ValueError("OOS_AUDIT_URI_RECORD_ID_REQUIRED")
    if _is_legacy_candidate_fragment(fragment) and not allow_legacy_candidate_fragment:
        raise ValueError(f"LEGACY_OOS_AUDIT_URI_AMBIGUOUS:{uri}")

    matched: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if _is_legacy_candidate_fragment(fragment):
                if str(record.get("candidate_hash") or "") == fragment:
                    matched.append(record)
            elif str(record.get("audit_record_id") or "") == fragment:
                return record
    if _is_legacy_candidate_fragment(fragment) and matched:
        # 仅迁移/排障场景使用：显式 opt-in 后返回写入时间最新的一条
        return max(matched, key=lambda item: str(item.get("audit_written_at") or ""))
    raise FileNotFoundError(f"OOS_AUDIT_RECORD_NOT_FOUND:{uri}")


def migrate_legacy_oos_audit_uri(
    uri: str,
    root: str | Path | None = None,
    *,
    research_run_id: str | None = None,
    event: str | None = None,
) -> str:
    """把旧 Candidate Hash URI 迁移为精确 Record ID URI。

    必须结合 research_run_id / event 过滤到唯一记录；无法唯一确定时抛错，
    不允许静默选择最后一条。
    """
    path, fragment = resolve_oos_audit_uri(uri, root=root)
    if not fragment:
        raise ValueError("OOS_AUDIT_URI_RECORD_ID_REQUIRED")
    if not _is_legacy_candidate_fragment(fragment):
        return uri  # 已是新格式
    matched: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if str(record.get("candidate_hash") or "") != fragment:
                continue
            if research_run_id and str(record.get("research_run_id") or "") != research_run_id:
                continue
            if event and str(record.get("event") or "") != event:
                continue
            matched.append(record)
    if len(matched) != 1:
        raise ValueError(
            f"LEGACY_OOS_AUDIT_URI_AMBIGUOUS:{uri}:matched={len(matched)}"
        )
    record_id = str(matched[0].get("audit_record_id") or "")
    if not record_id:
        raise ValueError(f"LEGACY_OOS_AUDIT_RECORD_ID_MISSING:{uri}")
    relative = uri[len(AUDIT_URI_SCHEME):].split("#")[0]
    return f"{AUDIT_URI_SCHEME}{relative}#{record_id}"


__all__ = [
    "AUDIT_ROOT",
    "AUDIT_URI_SCHEME",
    "AuditWriteResult",
    "append_oos_audit",
    "build_audit_record_id",
    "resolve_oos_audit_uri",
    "read_oos_audit",
    "migrate_legacy_oos_audit_uri",
]

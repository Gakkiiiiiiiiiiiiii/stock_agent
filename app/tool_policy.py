from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class PermissionLevel(str, Enum):
    READ = "READ"
    COMPUTE = "COMPUTE"
    PROPOSE_WRITE = "PROPOSE_WRITE"
    CONFIRMED_WRITE = "CONFIRMED_WRITE"
    ADMIN = "ADMIN"


@dataclass(frozen=True)
class ToolPolicy:
    permission: PermissionLevel
    timeout_seconds: float = 30.0
    output_limit_bytes: int = 64_000
    requires_confirmation: bool = False


class ToolPolicyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProposalStore:
    """Lightweight local proposal store for single-user deployments."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("PROPOSAL_STORE_PATH", "storage/runtime/proposals.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def create(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        proposal_id = str(uuid.uuid4())
        diff_hash = self._hash_payload(payload)
        row = {
            "proposal_id": proposal_id,
            "tool_name": tool_name,
            "payload": payload,
            "diff_hash": diff_hash,
            "status": "PENDING",
            "created_at": int(time.time()),
        }
        self._append(row)
        return {"proposal_id": proposal_id, "diff_hash": diff_hash, "diff": payload, "status": "PENDING"}

    def approve(self, proposal_id: str, ttl_seconds: int = 600) -> dict[str, Any]:
        row = self.get(proposal_id)
        if row is None:
            raise ToolPolicyError("PROPOSAL_NOT_FOUND", f"proposal not found: {proposal_id}")
        token_payload = {
            "proposal_id": proposal_id,
            "tool_name": row["tool_name"],
            "diff_hash": row["diff_hash"],
            "expires_at": int(time.time()) + ttl_seconds,
            "nonce": str(uuid.uuid4()),
        }
        token = self._hash_payload(token_payload)
        approved = dict(row)
        approved.update({"status": "APPROVED", "confirmation_token": token, "token_payload": token_payload})
        self._append(approved)
        return {"proposal_id": proposal_id, "confirmation_token": token, "expires_at": token_payload["expires_at"]}

    def verify(self, tool_name: str, payload: dict[str, Any], token: str | None) -> str:
        if not token:
            raise ToolPolicyError("CONFIRMATION_REQUIRED", f"tool {tool_name} requires confirmation_token")
        diff_hash = self._hash_payload(payload)
        row = next((item for item in reversed(self._read_all()) if item.get("confirmation_token") == token), None)
        if row is None:
            raise ToolPolicyError("CONFIRMATION_INVALID", "confirmation token is invalid or already used")
        status = row.get("status")
        if status in {"USED", "REVOKED"}:
            raise ToolPolicyError("CONFIRMATION_INVALID", "confirmation token is invalid or already used")
        if status != "APPROVED":
            raise ToolPolicyError("CONFIRMATION_INVALID", "confirmation token is invalid or already used")
        token_payload = row.get("token_payload") or {}
        if token_payload.get("tool_name") != tool_name or token_payload.get("diff_hash") != diff_hash:
            raise ToolPolicyError("CONFIRMATION_MISMATCH", "confirmation token is not bound to this tool payload")
        if int(token_payload.get("expires_at") or 0) < int(time.time()):
            raise ToolPolicyError("CONFIRMATION_EXPIRED", "confirmation token expired")
        used = dict(row)
        used["status"] = "USED"
        self._append(used)
        return str(row["proposal_id"])

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        for row in reversed(self._read_all()):
            if row.get("proposal_id") == proposal_id:
                return row
        return None

    def _append(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    @staticmethod
    def _hash_payload(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ToolAuditor:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("TOOL_AUDIT_LOG_PATH", "storage/runtime/tool_audit.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, row: dict[str, Any]) -> None:
        redacted = self._redact(row)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(redacted, ensure_ascii=False, sort_keys=True) + "\n")

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if any(token in str(key).lower() for token in ("key", "token", "cookie", "secret", "password")):
                    out[key] = "***REDACTED***"
                else:
                    out[key] = cls._redact(item)
            return out
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value

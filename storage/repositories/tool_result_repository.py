"""ToolResult Snapshot 仓储（详细修改方案 §13）。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select

from storage.bootstrap import create_all
from storage.db import session_scope
from storage.models.tool_result import ToolResultSnapshot


def hash_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class ToolResultRepository:
    def record(
        self,
        *,
        tool_id: str,
        request: Any,
        response: Any,
        tool_version: str | None = None,
        decision_id: str | None = None,
        agent_run_id: str | None = None,
        snapshot_refs: list | None = None,
        latency_ms: float | None = None,
        status: str = "OK",
    ) -> ToolResultSnapshot:
        create_all()
        with session_scope() as session:
            snapshot = ToolResultSnapshot(
                tool_id=tool_id,
                tool_version=tool_version,
                request_hash=hash_payload(request),
                response_hash=hash_payload(response),
                snapshot_refs=list(snapshot_refs or []),
                latency_ms=latency_ms,
                status=status,
                response_payload=response if isinstance(response, dict) else {"value": response},
                decision_id=decision_id,
                agent_run_id=agent_run_id,
            )
            session.add(snapshot)
            session.flush()
            session.refresh(snapshot)
            return snapshot

    def find_by_request(self, tool_id: str, request: Any) -> ToolResultSnapshot | None:
        """EXACT_REPLAY：按 tool_id + request_hash 复用历史结果（不重新联网）。"""
        create_all()
        request_hash = hash_payload(request)
        with session_scope() as session:
            return session.execute(
                select(ToolResultSnapshot)
                .where(ToolResultSnapshot.tool_id == tool_id, ToolResultSnapshot.request_hash == request_hash)
                .order_by(ToolResultSnapshot.created_at.desc())
            ).scalars().first()

    def list_for_decision(self, decision_id: str) -> list[ToolResultSnapshot]:
        create_all()
        with session_scope() as session:
            return list(
                session.scalars(
                    select(ToolResultSnapshot)
                    .where(ToolResultSnapshot.decision_id == decision_id)
                    .order_by(ToolResultSnapshot.created_at)
                )
            )


__all__ = ["ToolResultRepository", "hash_payload"]

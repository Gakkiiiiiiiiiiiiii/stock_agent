"""ToolResult Snapshot 数据模型（详细修改方案 §13/§25）。

所有影响决策的工具调用必须保存 request/response 哈希与快照引用，
EXACT_REPLAY 默认不重新联网，而是使用历史 ToolResult。
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


class ToolResultSnapshot(Base):
    __tablename__ = "tool_result_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_result_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=lambda: str(uuid4()))
    decision_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    tool_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tool_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_refs: Mapped[list] = mapped_column(JSON, default=list)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK", index=True)
    response_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


__all__ = ["ToolResultSnapshot"]

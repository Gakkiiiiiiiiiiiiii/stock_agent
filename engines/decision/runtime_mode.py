"""RuntimeMode（详细修改方案 §4）。

fallback 不得是"隐式另一套算法"：任何决策都必须显式记录运行模式，
进入 DecisionSnapshot 的 runtime 段。
"""
from __future__ import annotations

from enum import Enum


class RuntimeMode(str, Enum):
    PRIMARY_AGENT = "PRIMARY_AGENT"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"
    DEGRADED_AGENT = "DEGRADED_AGENT"
    REPLAY = "REPLAY"


FALLBACK_REASONS = frozenset(
    {
        "MODEL_UNAVAILABLE",
        "MODEL_TIMEOUT",
        "MODEL_ERROR",
        "BUDGET_EXCEEDED",
        "TOOL_UNAVAILABLE",
        "MANUAL",
    }
)


def build_runtime_segment(
    mode: RuntimeMode | str,
    *,
    fallback_reason: str | None = None,
    supervisor_version: str | None = None,
) -> dict:
    """构造 DecisionSnapshot.runtime 段（§4：fallback_used/fallback_reason 必须显式）。"""
    resolved = RuntimeMode(mode)
    fallback_used = resolved in (RuntimeMode.DETERMINISTIC_FALLBACK, RuntimeMode.DEGRADED_AGENT)
    if fallback_used and fallback_reason and fallback_reason not in FALLBACK_REASONS:
        raise ValueError(f"未知 fallback_reason: {fallback_reason}")
    return {
        "runtime_mode": resolved.value,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason if fallback_used else None,
        "supervisor_version": supervisor_version,
    }


__all__ = ["RuntimeMode", "FALLBACK_REASONS", "build_runtime_segment"]

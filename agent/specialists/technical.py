from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class TechnicalSpecialist(ToolSpecialist):
    role = AgentRole.TECHNICAL
    def __call__(self, task: AgentTask, _shared):
        symbols = list(self.context.get("candidate_symbols") or [])
        if not symbols:
            return self.artifact(task, {}, ["NO_CANDIDATE_SYMBOLS"], 0)
        technical = self.call("scan_technical_rules", {"symbols": symbols})
        signal = technical.get("signal") if isinstance(technical, dict) else None
        return self.artifact(task, {"technical": technical, "opinions": {"technical_signal": signal} if signal is not None else {}}, tool_calls=1)

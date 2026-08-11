from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class TechnicalSpecialist(ToolSpecialist):
    role = AgentRole.TECHNICAL
    def __call__(self, task: AgentTask, _shared):
        symbols = list(self.context.get("candidate_symbols") or [])
        if not symbols:
            return self.artifact(task, {}, ["NO_CANDIDATE_SYMBOLS"], 0)
        return self.artifact(task, {"technical": self.call("scan_technical_rules", {"symbols": symbols})}, tool_calls=1)

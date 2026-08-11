from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class PortfolioSpecialist(ToolSpecialist):
    role = AgentRole.PORTFOLIO
    def __call__(self, task: AgentTask, _shared):
        payload = {"candidates": list(self.context.get("candidates") or []), "positions": list(self.context.get("positions") or []), "context": dict(self.context.get("portfolio_context") or {})}
        return self.artifact(task, {"portfolio": self.call("construct_portfolio_v2", payload)}, tool_calls=1)

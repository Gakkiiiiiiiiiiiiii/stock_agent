from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class RiskSpecialist(ToolSpecialist):
    role = AgentRole.RISK
    def __call__(self, task: AgentTask, _shared):
        # Risk may veto through deterministic result, but never proposes weights.
        result = self.call("evaluate_portfolio_risk", {"positions": list(self.context.get("positions") or [])})
        veto = bool(result.get("risk_level") in {"high", "critical"} or result.get("veto"))
        return self.artifact(task, {"risk": result, "veto": veto}, tool_calls=1)

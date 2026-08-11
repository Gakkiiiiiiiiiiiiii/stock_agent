from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class RiskSpecialist(ToolSpecialist):
    role = AgentRole.RISK
    def __call__(self, task: AgentTask, shared):
        # Risk may veto through deterministic result, but never proposes weights.
        upstream = {task_id: artifact.conclusion for task_id, artifact in shared.dependency_artifacts(task.task_id).items()}
        portfolio = next((value.get("portfolio") for value in upstream.values() if isinstance(value, dict) and value.get("portfolio")), {})
        positions = list(portfolio.get("positions") or portfolio.get("targets") or self.context.get("positions") or []) if isinstance(portfolio, dict) else list(self.context.get("positions") or [])
        result = self.call("evaluate_portfolio_risk", {"positions": positions, "portfolio": portfolio})
        veto = bool(result.get("risk_level") in {"high", "critical"} or result.get("veto"))
        return self.artifact(task, {"risk": result, "veto": veto, "upstream_artifacts": upstream, "opinions": {"risk": result.get("risk_level") if isinstance(result, dict) else None}}, tool_calls=1)

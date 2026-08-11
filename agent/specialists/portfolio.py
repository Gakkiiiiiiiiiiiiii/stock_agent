from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class PortfolioSpecialist(ToolSpecialist):
    role = AgentRole.PORTFOLIO
    def __call__(self, task: AgentTask, shared):
        upstream = {task_id: artifact.conclusion for task_id, artifact in shared.dependency_artifacts(task.task_id).items()}
        candidates = list(self.context.get("candidates") or [])
        technical = next((value.get("technical") for value in upstream.values() if isinstance(value, dict) and value.get("technical")), None)
        if technical and isinstance(technical, dict):
            candidates = list(technical.get("candidates") or technical.get("ranked") or candidates)
        payload = {"candidates": candidates, "positions": list(self.context.get("positions") or []), "context": {**dict(self.context.get("portfolio_context") or {}), "upstream": upstream}}
        result = self.call("construct_portfolio_v2", payload)
        return self.artifact(task, {"portfolio": result, "upstream_artifacts": upstream}, tool_calls=1)

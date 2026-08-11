from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class FactorSpecialist(ToolSpecialist):
    role = AgentRole.FACTOR
    def __call__(self, task: AgentTask, _shared):
        # Existing factor tools are research jobs, not an on-demand score API.
        return self.artifact(task, {"factor_evidence": self.context.get("factor_evidence", [])}, ["FACTOR_ON_DEMAND_NOT_CONFIGURED"], 0)

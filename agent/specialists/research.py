from __future__ import annotations

from agent.contracts import AgentRole, AgentTask
from agent.specialists.base import ToolSpecialist


class ResearchSpecialist(ToolSpecialist):
    role = AgentRole.RESEARCH
    def __call__(self, task: AgentTask, _shared):
        result = self.call("retrieve_relevant_context", {"query": task.objective, "task_type": task.task_type})
        return self.artifact(task, {"retrieval": result}, tool_calls=1)

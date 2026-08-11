from __future__ import annotations

from datetime import UTC, datetime

from agent.contracts import AgentArtifact, AgentRole, AgentTask, TaskStatus


class ToolSpecialist:
    role: AgentRole

    def __init__(self, registry, context: dict | None = None) -> None:
        self.registry, self.context = registry, context or {}

    def call(self, name: str, payload: dict | None = None) -> dict:
        return self.registry.execute(name, payload or {})

    def artifact(self, task: AgentTask, conclusion: dict, warnings: list[str] | None = None, tool_calls: int = 0) -> AgentArtifact:
        return AgentArtifact(agent=self.role, task_id=task.task_id, status=TaskStatus.SUCCESS, conclusion=conclusion, data_as_of=datetime.now(UTC), warnings=warnings or [], confidence=.7 if not warnings else .4, tool_calls=tool_calls)

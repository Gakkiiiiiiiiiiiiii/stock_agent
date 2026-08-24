from __future__ import annotations

from datetime import UTC, datetime

from agent.contracts import AgentRole, AgentTask, SpecialistArtifact, SpecialistStatus, ToolUsage


class ToolSpecialist:
    role: AgentRole

    def __init__(self, registry, context: dict | None = None) -> None:
        self.registry, self.context = registry, context or {}

    def call(self, name: str, payload: dict | None = None) -> dict:
        return self.registry.execute(name, payload or {})

    def artifact(self, task: AgentTask, conclusion: dict, warnings: list[str] | None = None, tool_calls: int = 0, *, unknowns: list[str] | None = None, evidence_refs: list[str] | None = None, status: SpecialistStatus | None = None) -> SpecialistArtifact:
        warnings = warnings or []
        refs = list(evidence_refs or [])
        unknown_list = list(unknowns or warnings)
        if not refs and "EVIDENCE_REFS_MISSING" not in unknown_list:
            unknown_list.append("EVIDENCE_REFS_MISSING")
        resolved_status = status or (SpecialistStatus.DEGRADED if unknown_list else SpecialistStatus.SUCCESS)
        return SpecialistArtifact(specialist=self.role, task_id=task.task_id, status=resolved_status, conclusion=conclusion, evidence_refs=refs, warnings=warnings, unknowns=unknown_list, confidence=.7 if not unknown_list else .4, tool_usage=ToolUsage(calls=tool_calls))

"""P2 Supervisor: bounded DAG coordination, not free-form agent chat."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from financial_agent.config import load_yaml_config

from agent.contracts import AgentArtifact, AgentConflict, AgentRole, AgentTask, TaskStatus
from agent.shared_context import BudgetExceeded, SharedContext
from agent.task_graph import TaskGraph

Specialist = Callable[[AgentTask, SharedContext], AgentArtifact]

DOMAIN_OWNERS = {
    "market_regime": AgentRole.MARKET,
    "sector_strength": AgentRole.MARKET,
    "technical_signal": AgentRole.TECHNICAL,
    "factor_score": AgentRole.FACTOR,
    "portfolio": AgentRole.PORTFOLIO,
    "risk": AgentRole.RISK,
    "review": AgentRole.REVIEW,
}


def load_multi_agent_config() -> dict:
    try:
        return dict(load_yaml_config("multi_agent.yaml").get("multi_agent") or {})
    except FileNotFoundError:
        return {"max_agents_per_run": 8, "max_parallel_agents": 4, "max_total_tool_calls": 60, "max_total_llm_tokens": 60_000}


class Supervisor:
    def __init__(self, specialists: dict[AgentRole, Specialist], config: dict | None = None) -> None:
        self.specialists = dict(specialists)
        self.config = {**load_multi_agent_config(), **(config or {})}

    def run(self, graph: TaskGraph) -> dict:
        if len(graph.tasks) > int(self.config["max_agents_per_run"]):
            raise ValueError("max_agents_per_run exceeded")
        shared = SharedContext(int(self.config["max_total_tool_calls"]), int(self.config["max_total_llm_tokens"]))
        shared.set_dependencies({task_id: graph.dependencies(task_id) for task_id in graph.tasks})
        errors: list[dict] = []
        for layer in graph.topological_layers():
            with ThreadPoolExecutor(max_workers=min(len(layer), int(self.config["max_parallel_agents"]))) as pool:
                futures = {}
                for task_id in layer:
                    task = graph.tasks[task_id]
                    try:
                        shared.reserve_budget(task.tool_budget, task.token_budget)
                    except BudgetExceeded as exc:
                        task.status = TaskStatus.BUDGET_EXHAUSTED
                        errors.append({"task_id": task_id, "code": "BUDGET_EXHAUSTED", "message": str(exc)})
                        continue
                    futures[pool.submit(self._run_one, task, shared)] = (task_id, task.tool_budget, task.token_budget)
                for future in as_completed(futures):
                    task_id, reserved_tools, reserved_tokens = futures[future]
                    try:
                        artifact = future.result()
                        shared.commit_budget(reserved_tools, reserved_tokens, artifact)
                    except BudgetExceeded as exc:
                        shared.release_budget(reserved_tools, reserved_tokens)
                        graph.tasks[task_id].status = TaskStatus.BUDGET_EXHAUSTED
                        errors.append({"task_id": task_id, "code": "BUDGET_EXHAUSTED", "message": str(exc)})
                    except Exception as exc:  # noqa: BLE001
                        shared.release_budget(reserved_tools, reserved_tokens)
                        graph.tasks[task_id].status = TaskStatus.FAILED
                        errors.append({"task_id": task_id, "code": type(exc).__name__, "message": str(exc)})
        artifacts = shared.artifacts()
        conflicts = self._detect_conflicts(artifacts)
        return {"artifacts": [item.model_dump(mode="json") for item in artifacts], "errors": errors, "conflicts": [item.model_dump(mode="json") for item in conflicts], "usage": shared.usage()}

    def _run_one(self, task: AgentTask, shared: SharedContext) -> AgentArtifact:
        # task_type describes the business task (e.g. daily_market_decision),
        # while assigned_agent is the specialist role. Retain the v1 fallback
        # so persisted/test tasks using a role as task_type remain replayable.
        role = task.assigned_agent or AgentRole(task.task_type)
        specialist = self.specialists.get(role)
        if specialist is None:
            raise ValueError(f"unregistered specialist: {role}")
        task.status = TaskStatus.RUNNING
        artifact = specialist(task, shared)
        if artifact.agent != role or artifact.task_id != task.task_id:
            raise ValueError("specialist artifact identity mismatch")
        task.status = artifact.status
        return artifact

    @staticmethod
    def resolve_conflict(conflict: AgentConflict) -> AgentConflict:
        owner = DOMAIN_OWNERS.get(conflict.dimension)
        opinions = list(conflict.opinions)
        selected = next((item for item in opinions if item.get("agent") == (owner.value if owner else None)), None)
        if selected is None:
            selected = max(opinions, key=lambda item: (float(item.get("confidence", 0)), str(item.get("agent", ""))), default=None)
        if selected is not None:
            conflict.resolved_value = selected.get("value")
            conflict.resolved_by = str(selected.get("agent"))
        return conflict

    def _detect_conflicts(self, artifacts: list[AgentArtifact]) -> list[AgentConflict]:
        opinions: dict[str, list[dict]] = {}
        for artifact in artifacts:
            for dimension, value in artifact.conclusion.get("opinions", {}).items():
                opinions.setdefault(str(dimension), []).append({"agent": artifact.agent.value, "value": value, "confidence": artifact.confidence})
        conflicts = []
        for dimension, values in opinions.items():
            if len({repr(item["value"]) for item in values}) > 1:
                conflicts.append(self.resolve_conflict(AgentConflict(dimension=dimension, opinions=values)))
        return conflicts

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
    def __init__(self, specialists: dict[AgentRole, Specialist], config: dict | None = None, repository=None) -> None:
        self.specialists = dict(specialists)
        self.config = {**load_multi_agent_config(), **(config or {})}
        self.repository = repository

    def run(self, graph: TaskGraph) -> dict:
        if len(graph.tasks) > int(self.config["max_agents_per_run"]):
            raise ValueError("max_agents_per_run exceeded")
        shared = SharedContext(int(self.config["max_total_tool_calls"]), int(self.config["max_total_llm_tokens"]))
        shared.set_dependencies({task_id: graph.dependencies(task_id) for task_id in graph.tasks})
        first_task = next(iter(graph.tasks.values()), None)
        run = self.repository.create_agent_run(
            task_type=first_task.task_type if first_task else "unknown",
            objective=first_task.objective if first_task else "",
            status="RUNNING",
            as_of=first_task.as_of if first_task else None,
            participating_agents=[task.assigned_agent.value for task in graph.tasks.values() if task.assigned_agent],
        ) if self.repository is not None else None
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
                        self._persist_subtask(run, artifact)
                    except BudgetExceeded as exc:
                        shared.release_budget(reserved_tools, reserved_tokens)
                        graph.tasks[task_id].status = TaskStatus.BUDGET_EXHAUSTED
                        errors.append({"task_id": task_id, "code": "BUDGET_EXHAUSTED", "message": str(exc)})
                        self._persist_failed_subtask(run, graph.tasks[task_id], TaskStatus.BUDGET_EXHAUSTED, str(exc))
                    except Exception as exc:  # noqa: BLE001
                        shared.release_budget(reserved_tools, reserved_tokens)
                        graph.tasks[task_id].status = TaskStatus.FAILED
                        errors.append({"task_id": task_id, "code": type(exc).__name__, "message": str(exc)})
                        self._persist_failed_subtask(run, graph.tasks[task_id], TaskStatus.FAILED, str(exc))
        artifacts = shared.artifacts()
        conflicts = self._detect_conflicts(artifacts)
        usage = shared.usage()
        if run is not None:
            for conflict in conflicts:
                self.repository.add_conflict(
                    agent_run_id=run.id, dimension=conflict.dimension, opinions=conflict.opinions,
                    resolution_policy=conflict.resolution_policy, resolved_value={"value": conflict.resolved_value}, resolved_by=conflict.resolved_by,
                )
            self.repository.update_agent_run(run.id, status="FAILED" if errors else "SUCCESS", usage=usage)
        return {"agent_run_id": run.id if run is not None else None, "artifacts": [item.model_dump(mode="json") for item in artifacts], "errors": errors, "conflicts": [item.model_dump(mode="json") for item in conflicts], "usage": usage}

    def _persist_subtask(self, run, artifact: AgentArtifact) -> None:
        if run is not None:
            self.repository.add_subtask(
                id=artifact.task_id, agent_run_id=run.id, agent=artifact.agent.value, status=artifact.status.value,
                conclusion=artifact.conclusion, evidence_refs=artifact.evidence_refs, confidence=artifact.confidence,
                usage={"tool_calls": artifact.tool_calls, "token_used": artifact.token_used, "latency_ms": artifact.latency_ms},
            )

    def _persist_failed_subtask(self, run, task: AgentTask, status: TaskStatus, error: str) -> None:
        if run is not None:
            role = task.assigned_agent or AgentRole(task.task_type)
            self.repository.add_subtask(id=task.task_id, agent_run_id=run.id, agent=role.value, status=status.value, conclusion={"error": error}, evidence_refs=[], confidence=0.0, usage={})

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

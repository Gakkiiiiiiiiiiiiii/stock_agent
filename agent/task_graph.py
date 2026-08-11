"""A bounded, deterministic DAG executor for specialist tasks."""
from __future__ import annotations

from collections import defaultdict

from agent.contracts import AgentTask


class TaskGraphError(ValueError):
    pass


class TaskGraph:
    def __init__(self) -> None:
        self.tasks: dict[str, AgentTask] = {}
        self._dependencies: dict[str, set[str]] = defaultdict(set)

    def add_task(self, task: AgentTask, depends_on: list[str] | None = None) -> None:
        if task.task_id in self.tasks:
            raise TaskGraphError(f"duplicate task_id: {task.task_id}")
        self.tasks[task.task_id] = task
        deps = set(depends_on or [])
        unknown = deps - set(self.tasks)
        if unknown:
            raise TaskGraphError(f"unknown dependency: {sorted(unknown)}")
        self._dependencies[task.task_id] = deps
        self.topological_layers()  # fail immediately on a cycle

    def dependencies(self, task_id: str) -> set[str]:
        return set(self._dependencies.get(task_id, set()))

    def topological_layers(self) -> list[list[str]]:
        remaining = {key: set(value) for key, value in self._dependencies.items()}
        layers: list[list[str]] = []
        while remaining:
            ready = sorted(key for key, deps in remaining.items() if not deps)
            if not ready:
                raise TaskGraphError("task graph contains a cycle")
            layers.append(ready)
            for key in ready:
                remaining.pop(key)
            for deps in remaining.values():
                deps.difference_update(ready)
        return layers


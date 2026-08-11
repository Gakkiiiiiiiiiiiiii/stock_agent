"""Thread-safe artifact and budget store shared by an agent DAG run."""
from __future__ import annotations

from threading import Lock

from agent.contracts import AgentArtifact


class BudgetExceeded(RuntimeError):
    pass


class SharedContext:
    def __init__(self, max_tools: int, max_tokens: int) -> None:
        self.max_tools = max_tools
        self.max_tokens = max_tokens
        self._tool_used = 0
        self._token_used = 0
        self._artifacts: dict[str, AgentArtifact] = {}
        self._dependencies: dict[str, set[str]] = {}
        self._lock = Lock()

    def record(self, artifact: AgentArtifact) -> None:
        with self._lock:
            if self._tool_used + artifact.tool_calls > self.max_tools:
                raise BudgetExceeded("max_total_tool_calls exceeded")
            if self._token_used + artifact.token_used > self.max_tokens:
                raise BudgetExceeded("max_total_llm_tokens exceeded")
            self._tool_used += artifact.tool_calls
            self._token_used += artifact.token_used
            self._artifacts[artifact.task_id] = artifact

    def reserve_budget(self, tool_calls: int = 0, tokens: int = 0) -> None:
        """Reserve before a specialist starts so parallel work cannot overspend."""
        with self._lock:
            if self._tool_used + tool_calls > self.max_tools or self._token_used + tokens > self.max_tokens:
                raise BudgetExceeded("shared budget reservation rejected")
            self._tool_used += tool_calls
            self._token_used += tokens

    def commit_budget(self, reserved_tools: int, reserved_tokens: int, artifact: AgentArtifact) -> None:
        """Replace a reservation with actual usage without double counting."""
        with self._lock:
            self._tool_used -= reserved_tools
            self._token_used -= reserved_tokens
        self.record(artifact)

    def release_budget(self, tool_calls: int = 0, tokens: int = 0) -> None:
        with self._lock:
            self._tool_used = max(0, self._tool_used - tool_calls)
            self._token_used = max(0, self._token_used - tokens)

    def set_dependencies(self, dependencies: dict[str, set[str]]) -> None:
        with self._lock:
            self._dependencies = {task_id: set(values) for task_id, values in dependencies.items()}

    def dependency_artifacts(self, task_id: str) -> dict[str, AgentArtifact]:
        with self._lock:
            return {dep: self._artifacts[dep] for dep in self._dependencies.get(task_id, set()) if dep in self._artifacts}

    def artifact(self, task_id: str) -> AgentArtifact | None:
        with self._lock:
            return self._artifacts.get(task_id)

    def artifacts(self) -> list[AgentArtifact]:
        with self._lock:
            return list(self._artifacts.values())

    def usage(self) -> dict[str, int]:
        with self._lock:
            return {"tool_calls": self._tool_used, "tokens": self._token_used}

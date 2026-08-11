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

    def artifact(self, task_id: str) -> AgentArtifact | None:
        with self._lock:
            return self._artifacts.get(task_id)

    def artifacts(self) -> list[AgentArtifact]:
        with self._lock:
            return list(self._artifacts.values())

    def usage(self) -> dict[str, int]:
        with self._lock:
            return {"tool_calls": self._tool_used, "tokens": self._token_used}

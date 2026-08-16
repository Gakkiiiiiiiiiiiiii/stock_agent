"""AgentOrchestrator 兼容 Facade（P0 A-02）。

所有 public 方法只委托唯一 ``DecisionRuntime``；public facade 禁止直接调用
ClaudeAgent.run / LocalFallbackOrchestrator 的决策方法。ClaudeAgent 与
LocalFallbackOrchestrator 只能作为 DecisionRuntime 内部 execution adapter：

    LLM execution authority ≠ Decision authority
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

from app.decision_runtime import DecisionRuntime


class AgentOrchestrator:
    """兼容 Facade：历史调用方（app.dependencies / routers）保持不变。"""

    def __init__(self, runtime: DecisionRuntime | None = None) -> None:
        self.runtime = runtime or DecisionRuntime()
        # 仅暴露 runtime 内部 adapter 的属性引用（admin 工具审批等旧端点依赖），
        # facade 的 public 方法不得通过它们发起任何决策调用。
        self.claude_agent = self.runtime.claude_agent
        self.fallback = self.runtime.fallback

    def analyze_stock(self, symbol: str, as_of: date | None = None, patterns: list[str] | None = None) -> dict:
        return self.runtime.analyze_stock(symbol, as_of=as_of, patterns=patterns)

    def analyze_theme(self, theme_name: str) -> dict:
        return self.runtime.analyze_theme(theme_name)

    def daily_scan(self, scan_date: date | None = None, mode: str = "after_close") -> dict:
        return self.runtime.daily_scan(scan_date=scan_date, mode=mode)

    def run_agent(
        self,
        query: str,
        context: dict | None = None,
        skill: str | None = None,
        emit: Callable[[str, dict], None] | None = None,
    ) -> dict:
        return self.runtime.run(query, context=context, skill=skill, emit=emit)

    def agent_enabled(self) -> bool:
        return self.runtime.agent_enabled()

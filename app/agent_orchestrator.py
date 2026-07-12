from __future__ import annotations

from collections.abc import Callable
from datetime import date

from app.claude_agent import ClaudeAgent
from app.fallback_orchestrator import LocalFallbackOrchestrator


class AgentOrchestrator:
    def __init__(self) -> None:
        self.claude_agent = ClaudeAgent()
        self.fallback = LocalFallbackOrchestrator()

    def analyze_stock(self, symbol: str, as_of: date | None = None, patterns: list[str] | None = None) -> dict:
        if self.agent_enabled():
            result = self.claude_agent.run(
                user_query=f"分析股票 {symbol} 当前是否存在技术机会，并给出风险和操作条件。",
                context={"symbol": symbol, "date": str(as_of) if as_of else None, "patterns": patterns},
                force_skill="a-share-technical-analysis",
            )
            return {
                "symbol": symbol,
                "date": str(as_of) if as_of else None,
                "orchestration": "claude-style-agent",
                "selected_skill": result.selected_skill,
                "selection_reason": result.selection_reason,
                "tool_calls": result.tool_calls,
                "trace": result.trace,
                "report": result.report,
            },
        return self.fallback.analyze_stock(symbol, as_of=as_of, patterns=patterns)

    def analyze_theme(self, theme_name: str) -> dict:
        if self.agent_enabled():
            result = self.claude_agent.run(
                user_query=f"分析主题 {theme_name} 的投资逻辑是否成立，并输出催化、标的、触发和证伪条件。",
                context={"theme_name": theme_name},
                force_skill="industry-logic-research",
            )
            return {
                "theme_name": theme_name,
                "orchestration": "claude-style-agent",
                "selected_skill": result.selected_skill,
                "selection_reason": result.selection_reason,
                "tool_calls": result.tool_calls,
                "trace": result.trace,
                "report": result.report,
            }
        return self.fallback.analyze_theme(theme_name)

    def daily_scan(self, scan_date: date | None = None, mode: str = "after_close") -> dict:
        if self.agent_enabled():
            result = self.claude_agent.run(
                user_query=f"请完成 {str(scan_date or date.today())} {mode} 的每日市场扫描，输出强主题、候选方向、仓位建议和风险提示。",
                context={"date": str(scan_date) if scan_date else None, "mode": mode},
                force_skill="daily-market-decision",
            )
            return {
                "date": str(scan_date or date.today()),
                "mode": mode,
                "orchestration": "claude-style-agent",
                "selected_skill": result.selected_skill,
                "selection_reason": result.selection_reason,
                "tool_calls": result.tool_calls,
                "trace": result.trace,
                "report": result.report,
            }
        return self.fallback.daily_scan(scan_date=scan_date, mode=mode)

    def run_agent(
        self,
        query: str,
        context: dict | None = None,
        skill: str | None = None,
        emit: Callable[[str, dict], None] | None = None,
    ) -> dict:
        if self.agent_enabled():
            result = self.claude_agent.run(user_query=query, context=context, force_skill=skill, emit=emit)
            payload = {
                "orchestration": "claude-style-agent",
                "selected_skill": result.selected_skill,
                "selection_reason": result.selection_reason,
                "tool_calls": result.tool_calls,
                "trace": result.trace,
                "report": result.report,
            }
            if emit:
                emit("done", payload)
            return payload
        payload = {
            "orchestration": "local-fallback",
            "warning": "主模型未配置，当前无法运行 Claude-style Agent。请配置 AGENT_MODEL_* 或 ANALYSIS_MODEL_* 为 DeepSeek/OpenAI-compatible 模型。",
            "trace": {
                "selection_reason": "Primary agent model is unavailable.",
                "steps": [
                    {
                        "type": "warning",
                        "title": "Model unavailable",
                        "content": "AGENT_MODEL_* or ANALYSIS_MODEL_* is not configured, so the chat agent could not start.",
                    }
                ],
            },
        }
        if emit:
            emit("warning", {"message": payload["warning"]})
            emit("trace", {"step": payload["trace"]["steps"][0]})
            emit("done", payload)
        return payload

    @staticmethod
    def agent_enabled() -> bool:
        return ClaudeAgent().configured()

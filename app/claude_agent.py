from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.model_providers import AgentModelClient, AgentModelSettings
from app.skill_loader import SkillDefinition, format_skill_catalog, load_skills
from app.tool_registry import ClaudeToolRegistry


@dataclass
class ClaudeAgentResponse:
    selected_skill: str
    selection_reason: str
    report: str
    tool_calls: list[dict[str, Any]]
    trace: dict[str, Any]
    raw_text: str | None = None


@dataclass
class SkillSelectionDecision:
    skill: SkillDefinition
    reason: str


class ClaudeAgent:
    """
    Keep the ClaudeAgent name because the project still follows the Claude-style
    agent architecture: skills + controlled tools + orchestration loop.
    The underlying model, however, is an OpenAI-compatible provider such as DeepSeek.
    """

    def __init__(
        self,
        client: AgentModelClient | Any | None = None,
        tools: ClaudeToolRegistry | None = None,
        skills: list[SkillDefinition] | None = None,
        model: str | None = None,
        max_tool_rounds: int = 8,
    ) -> None:
        if client is not None:
            self.client = client
        elif model:
            self.client = AgentModelClient(
                settings=AgentModelSettings(
                    provider=AgentModelSettings.from_env().provider,
                    model=model,
                    base_url=AgentModelSettings.from_env().base_url,
                    api_key=AgentModelSettings.from_env().api_key,
                )
            )
        else:
            self.client = AgentModelClient()
        self.tool_registry = tools or ClaudeToolRegistry()
        self.skills = skills or load_skills()
        self.max_tool_rounds = max_tool_rounds

    def configured(self) -> bool:
        return bool(getattr(self.client, "available", lambda: False)())

    def run(
        self,
        user_query: str,
        context: dict[str, Any] | None = None,
        force_skill: str | None = None,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> ClaudeAgentResponse:
        if not self.configured():
            raise RuntimeError("Primary agent model is not configured")
        self._emit(emit, "status", {"message": "Selecting skill..."})
        decision = self._choose_skill(user_query, context, force_skill=force_skill)
        self._emit(
            emit,
            "selection",
            {
                "orchestration": "claude-style-agent",
                "selected_skill": decision.skill.slug,
                "selection_reason": decision.reason,
            },
        )
        self._emit(
            emit,
            "trace",
            {
                "step": {
                    "type": "skill_selection",
                    "title": "Skill selected",
                    "content": decision.reason,
                    "data": {"skill": decision.skill.slug},
                }
            },
        )
        self._emit(emit, "status", {"message": f"Running skill: {decision.skill.slug}"})
        report, tool_calls, trace_steps = self._run_skill(decision.skill, user_query, context, emit=emit)
        trace = {
            "selection_reason": decision.reason,
            "steps": [
                {
                    "type": "skill_selection",
                    "title": "Skill selected",
                    "content": decision.reason,
                    "data": {"skill": decision.skill.slug},
                },
                *trace_steps,
            ],
        }
        return ClaudeAgentResponse(
            selected_skill=decision.skill.slug,
            selection_reason=decision.reason,
            report=report,
            tool_calls=tool_calls,
            trace=trace,
            raw_text=report,
        )

    def _choose_skill(
        self,
        user_query: str,
        context: dict[str, Any] | None = None,
        force_skill: str | None = None,
    ) -> SkillSelectionDecision:
        if force_skill:
            for skill in self.skills:
                if skill.slug == force_skill or skill.name == force_skill:
                    return SkillSelectionDecision(skill=skill, reason=f"Skill forced by caller: {force_skill}")
            raise ValueError(f"unknown skill: {force_skill}")
        skill_catalog = format_skill_catalog(self.skills)
        response = self.client.create_chat_completion(
            system=(
                "You are the orchestration brain for a financial research agent. "
                "Pick exactly one skill that best matches the task. "
                f"Today's runtime date is {date.today().isoformat()}. "
                "Return only a JSON object like "
                '{"skill_slug":"...", "reason":"..."} '
                "with no markdown fences and no extra text."
            ),
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"User query:\n{user_query}\n\n"
                        f"Context:\n{json.dumps(context or {}, ensure_ascii=False)}\n\n"
                        f"Available skills:\n{skill_catalog}"
                    ),
                }
            ],
        )
        message = ((response.get("choices") or [{}])[0]).get("message", {})
        content = (message.get("content") or "").strip()
        try:
            payload = self._parse_json_object(content)
            selected = payload.get("skill_slug")
            if selected:
                for skill in self.skills:
                    if skill.slug == selected or skill.name == selected:
                        reason = str(payload.get("reason") or f"Model selected skill {selected} for this task.")
                        return SkillSelectionDecision(skill=skill, reason=reason)
        except Exception:
            pass
        fallback = self._fallback_choose_skill(user_query)
        if fallback is not None:
            return SkillSelectionDecision(skill=fallback, reason="Fallback keyword routing selected this skill.")
        raise RuntimeError("Primary agent model did not select a skill")

    def _run_skill(
        self,
        skill: SkillDefinition,
        user_query: str,
        context: dict[str, Any] | None = None,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        tool_calls: list[dict[str, Any]] = []
        trace_steps: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"User query:\n{user_query}\n\n"
                    f"Structured context:\n{json.dumps(context or {}, ensure_ascii=False)}"
                ),
            }
        ]
        system = (
            "You are the primary model running a Claude-style financial analysis agent framework. "
            "You must personally do skill execution planning, tool invocation, and final report writing. "
            "Use the provided tools for all deterministic computation and data retrieval. "
            "If the ask_research_model tool is available, you may use it as a subordinate helper, "
            "but you remain responsible for the final judgment. "
            f"Never fabricate missing market data. If data is insufficient, say so clearly. Today's runtime date is {date.today().isoformat()}.\n\n"
            f"Selected skill: {skill.slug}\n\n"
            f"Skill instructions:\n{skill.content}\n\n"
            "Before any tool call, you may write a short user-visible execution note. "
            "Keep it brief and factual. Do not reveal hidden chain-of-thought."
        )
        for round_index in range(1, self.max_tool_rounds + 1):
            self._emit(emit, "status", {"message": f"Planning round {round_index}..."})
            response = self.client.create_chat_completion(
                model=None,
                max_tokens=2048,
                system=system,
                tools=self.tool_registry.openai_tools(),
                messages=messages,
            )
            message = ((response.get("choices") or [{}])[0]).get("message", {})
            assistant_note = (message.get("content") or "").strip()
            if assistant_note:
                step = {
                    "type": "assistant_note",
                    "title": f"Round {round_index} note",
                    "content": assistant_note,
                }
                trace_steps.append(step)
                self._emit(emit, "trace", {"step": step})
            response_tool_calls = message.get("tool_calls") or []
            if not response_tool_calls:
                final_report = (message.get("content") or "").strip()
                step = {
                    "type": "final_answer",
                    "title": "Final answer",
                    "content": self._truncate_text(final_report),
                }
                trace_steps.append(step)
                self._emit(emit, "trace", {"step": step})
                for chunk in self._chunk_text(final_report):
                    self._emit(emit, "report_delta", {"delta": chunk})
                return final_report, tool_calls, trace_steps
            assistant_message = {"role": "assistant", "content": message.get("content") or "", "tool_calls": response_tool_calls}
            messages.append(assistant_message)
            for tool_call in response_tool_calls:
                function = tool_call.get("function", {})
                name = function.get("name")
                call_id = tool_call.get("id") or f"tool_{len(tool_calls) + 1}"
                arguments = json.loads(function.get("arguments") or "{}")
                call_step = {
                    "type": "tool_call",
                    "title": name,
                    "content": self._tool_call_summary(name, arguments),
                    "data": {"call_id": call_id, "input": arguments},
                }
                trace_steps.append(call_step)
                self._emit(emit, "trace", {"step": call_step})
                self._emit(emit, "tool_call", {"call_id": call_id, "name": name, "input": arguments})
                result = self.tool_registry.execute(name, arguments)
                tool_calls.append({"call_id": call_id, "name": name, "input": arguments, "output": result})
                result_step = {
                    "type": "tool_result",
                    "title": name,
                    "content": self._tool_result_summary(result),
                    "data": {"call_id": call_id, "output": result},
                }
                trace_steps.append(result_step)
                self._emit(emit, "trace", {"step": result_step})
                self._emit(emit, "tool_result", {"call_id": call_id, "name": name, "output": result})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        final_response = self.client.create_chat_completion(
            model=None,
            max_tokens=2048,
            system=(
                f"{system}\n\n"
                "You have already used enough tools. "
                "Do not call any more tools. "
                "Write the final answer directly using the collected evidence, "
                "and clearly state any remaining uncertainty."
            ),
            messages=messages,
        )
        final_message = ((final_response.get("choices") or [{}])[0]).get("message", {})
        final_content = (final_message.get("content") or "").strip()
        if final_content:
            step = {
                "type": "final_answer",
                "title": "Final answer",
                "content": self._truncate_text(final_content),
            }
            trace_steps.append(step)
            self._emit(emit, "trace", {"step": step})
            for chunk in self._chunk_text(final_content):
                self._emit(emit, "report_delta", {"delta": chunk})
            return final_content, tool_calls, trace_steps
        raise RuntimeError("Primary agent tool loop exceeded max rounds")

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
        return json.loads(text)

    def _fallback_choose_skill(self, user_query: str) -> SkillDefinition | None:
        query = user_query.lower()
        rules = [
            ("portfolio-construction", ["组合", "仓位", "持仓", "配仓", "防守", "标的"]),
            ("portfolio-risk-review", ["风控", "风险", "暴露", "集中度"]),
            ("industry-logic-research", ["主题", "产业链", "催化", "证伪", "黄金", "逻辑"]),
            ("market-regime-strategy-router", ["市场状态", "风格", "轮动", "退潮", "regime"]),
            ("a-share-technical-analysis", ["技术", "k线", "b1", "b2", "b3", "macd", "rps"]),
            ("post-trade-review", ["复盘", "交易后", "卖出", "买入原因"]),
            ("decision-conflict-resolver", ["冲突", "矛盾", "取舍", "分歧"]),
            ("daily-market-decision", ["每日", "日内", "扫描", "今日计划"]),
        ]
        for slug, keywords in rules:
            if any(keyword in query for keyword in keywords):
                for skill in self.skills:
                    if skill.slug == slug:
                        return skill
        return self.skills[0] if self.skills else None

    def _tool_call_summary(self, name: str, arguments: dict[str, Any]) -> str:
        description = self.tool_registry.describe_tool(name)
        if not arguments:
            return description
        return f"{description} Inputs: {self._truncate_text(json.dumps(arguments, ensure_ascii=False), limit=220)}"

    @staticmethod
    def _tool_result_summary(result: dict[str, Any]) -> str:
        if not result:
            return "Tool returned an empty result."
        if "error" in result:
            return f"Tool returned error: {result['error']}"
        keys = list(result.keys())[:6]
        return f"Tool returned keys: {', '.join(keys)}"

    @staticmethod
    def _truncate_text(value: str, limit: int = 280) -> str:
        text = (value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}..."

    @staticmethod
    def _chunk_text(value: str, chunk_size: int = 120) -> list[str]:
        text = value or ""
        if not text:
            return []
        return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]

    @staticmethod
    def _emit(emit: Callable[[str, dict[str, Any]], None] | None, event: str, payload: dict[str, Any]) -> None:
        if emit is None:
            return
        emit(event, payload)

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.skill_contract import SkillContractValidator, SkillContractViolation, SkillExecutionState
from app.skill_loader import SkillDefinition


class SkillExecutor:
    """Runs the model/tool loop and prevents a skill from bypassing its contract."""

    def __init__(self, client: Any, tool_registry: Any, default_max_tool_rounds: int = 8) -> None:
        self.client = client
        self.tool_registry = tool_registry
        self.default_max_tool_rounds = default_max_tool_rounds
        self.validator = SkillContractValidator()

    def run(
        self,
        skill: SkillDefinition,
        user_query: str,
        context: dict[str, Any] | None,
        system: str,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], SkillExecutionState]:
        state = SkillExecutionState(skill_slug=skill.slug)
        tool_calls: list[dict[str, Any]] = []
        trace_steps: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": f"User query:\n{user_query}\n\nStructured context:\n{json.dumps(context or {}, ensure_ascii=False)}",
        }]
        max_rounds = skill.execution.max_tool_rounds if skill.execution else self.default_max_tool_rounds
        for round_index in range(1, max_rounds + 1):
            state.round_index = round_index
            self._emit(emit, "status", {"message": f"Planning round {round_index}..."})
            response = self.client.create_chat_completion(
                model=None, max_tokens=2048, system=system, tools=self.tool_registry.openai_tools(), messages=messages
            )
            message = ((response.get("choices") or [{}])[0]).get("message", {})
            assistant_note = (message.get("content") or "").strip()
            response_tool_calls = message.get("tool_calls") or []
            if assistant_note and response_tool_calls:
                step = {"type": "assistant_note", "title": f"Round {round_index} note", "content": assistant_note}
                trace_steps.append(step)
                self._emit(emit, "trace", {"step": step})
            if not response_tool_calls:
                violations = self.validator.validate(skill, state, assistant_note)
                if not violations:
                    self._emit_final(assistant_note, trace_steps, emit)
                    return assistant_note, tool_calls, trace_steps, state
                if round_index == max_rounds:
                    raise SkillContractViolation(violations)
                step = {"type": "contract_violation", "title": "Skill contract incomplete", "content": " ".join(violations), "data": {"violations": violations}}
                trace_steps.append(step)
                self._emit(emit, "trace", {"step": step})
                messages.append({"role": "assistant", "content": assistant_note})
                messages.append({"role": "system", "content": self._violation_prompt(violations)})
                continue
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": response_tool_calls})
            for tool_call in response_tool_calls:
                function = tool_call.get("function", {})
                name = function.get("name")
                call_id = tool_call.get("id") or f"tool_{len(tool_calls) + 1}"
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                call_step = {"type": "tool_call", "title": name, "content": self._tool_call_summary(name, arguments), "data": {"call_id": call_id, "input": arguments}}
                trace_steps.append(call_step)
                self._emit(emit, "trace", {"step": call_step})
                self._emit(emit, "tool_call", {"call_id": call_id, "name": name, "input": arguments})
                result = self.tool_registry.execute(name, arguments)
                state.record_tool_result(name, result)
                tool_calls.append({"call_id": call_id, "name": name, "input": arguments, "output": result})
                result_step = {"type": "tool_result", "title": name, "content": self._tool_result_summary(result), "data": {"call_id": call_id, "output": result}}
                trace_steps.append(result_step)
                self._emit(emit, "trace", {"step": result_step})
                self._emit(emit, "tool_result", {"call_id": call_id, "name": name, "output": result})
                messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False, default=str)})
        raise SkillContractViolation(["Skill execution exhausted its tool-round budget."])

    @staticmethod
    def _violation_prompt(violations: list[str]) -> str:
        return "The skill contract is not complete. Do not produce a final answer yet. " + " ".join(violations) + " Call the missing tools, then provide every required report section."

    @staticmethod
    def _emit_final(final_text: str, trace_steps: list[dict[str, Any]], emit: Callable[[str, dict[str, Any]], None] | None) -> None:
        step = {"type": "final_answer", "title": "Final answer", "content": SkillExecutor._truncate_text(final_text)}
        trace_steps.append(step)
        SkillExecutor._emit(emit, "trace", {"step": step})
        for chunk in SkillExecutor._chunk_text(final_text):
            SkillExecutor._emit(emit, "report_delta", {"delta": chunk})

    def _tool_call_summary(self, name: str, arguments: dict[str, Any]) -> str:
        description = self.tool_registry.describe_tool(name)
        return description if not arguments else f"{description} Inputs: {self._truncate_text(json.dumps(arguments, ensure_ascii=False), 220)}"

    @staticmethod
    def _tool_result_summary(result: dict[str, Any]) -> str:
        if not result:
            return "Tool returned an empty result."
        if "error" in result:
            return f"Tool returned error: {result['error']}"
        return f"Tool returned keys: {', '.join(list(result.keys())[:6])}"

    @staticmethod
    def _truncate_text(value: str, limit: int = 280) -> str:
        value = (value or "").strip()
        return value if len(value) <= limit else f"{value[:limit].rstrip()}..."

    @staticmethod
    def _chunk_text(value: str, chunk_size: int = 120) -> list[str]:
        return [value[index : index + chunk_size] for index in range(0, len(value or ""), chunk_size)]

    @staticmethod
    def _emit(emit: Callable[[str, dict[str, Any]], None] | None, event: str, payload: dict[str, Any]) -> None:
        if emit:
            emit(event, payload)

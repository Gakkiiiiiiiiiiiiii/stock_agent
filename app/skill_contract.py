from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SkillOutputContract(BaseModel):
    required_sections: list[str] = Field(default_factory=list)
    json_schema_name: str | None = None


class SkillExecutionContract(BaseModel):
    required_tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    max_tool_rounds: int = 8
    min_tool_rounds: int = 0
    require_fresh_market_data: bool = False
    require_memory_lookup: bool = False
    require_regime: bool = False


class SkillExecutionState(BaseModel):
    """Auditable state used to enforce a skill's executable contract."""

    skill_slug: str
    round_index: int = 0
    called_tools: list[str] = Field(default_factory=list)
    tool_results: dict[str, list[dict]] = Field(default_factory=dict)
    market_data_timestamp: datetime | str | None = None
    regime_loaded: bool = False
    memory_loaded: bool = False
    contract_violations: list[str] = Field(default_factory=list)

    def record_tool_result(self, name: str, result: dict) -> None:
        self.called_tools.append(name)
        self.tool_results.setdefault(name, []).append(result)
        if name == "get_market_regime" and "error" not in result:
            self.regime_loaded = True
        if name in {"retrieve_relevant_context", "retrieve_memory"} and "error" not in result:
            self.memory_loaded = True
        if name in {"get_market_snapshot", "get_kline"} and "error" not in result:
            snapshot = result.get("snapshot", result)
            self.market_data_timestamp = snapshot.get("as_of") or snapshot.get("timestamp") or datetime.now().isoformat()


class SkillContractViolation(Exception):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


class SkillContractValidator:
    def validate(self, skill: "SkillDefinition", state: SkillExecutionState, final_text: str) -> list[str]:
        execution = skill.execution
        called = set(state.called_tools)
        violations: list[str] = []
        missing = [name for name in execution.required_tools if name not in called]
        if missing:
            violations.append(f"Required tools have not been called: {', '.join(missing)}.")
        forbidden = sorted(called.intersection(execution.forbidden_tools))
        if forbidden:
            violations.append(f"Forbidden tools were called: {', '.join(forbidden)}.")
        if state.round_index < execution.min_tool_rounds:
            violations.append(f"At least {execution.min_tool_rounds} tool rounds are required.")
        if execution.require_fresh_market_data and state.market_data_timestamp is None:
            violations.append("Fresh market data is required; call get_market_snapshot or get_kline.")
        if execution.require_memory_lookup and not state.memory_loaded:
            violations.append("Memory/context lookup is required; call retrieve_relevant_context or retrieve_memory.")
        if execution.require_regime and not state.regime_loaded:
            violations.append("Market regime is required; call get_market_regime.")
        for section in skill.output.required_sections:
            if f"### {section}" not in final_text and f"## {section}" not in final_text:
                violations.append(f"Final report is missing required section: {section}.")
        state.contract_violations = violations
        return violations


# Imported only for type checking at runtime-free module initialization.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.skill_loader import SkillDefinition

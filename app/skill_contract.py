from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from engines.market.exchange_calendar import ExchangeTradingCalendar
from engines.market.market_clock import MarketClock


CN_TZ = ZoneInfo("Asia/Shanghai")


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
    freshness: "FreshnessPolicy | None" = None


class FreshnessPolicy(BaseModel):
    max_age_minutes: int | None = None
    require_same_trading_day: bool = False
    require_after_market_open: bool = False


class ToolExecutionRecord(BaseModel):
    name: str
    call_id: str | None = None
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    result_meta: dict = Field(default_factory=dict)


class SkillExecutionState(BaseModel):
    """Auditable state used to enforce a skill's executable contract."""

    skill_slug: str
    round_index: int = 0
    called_tools: list[str] = Field(default_factory=list)
    successful_tools: list[str] = Field(default_factory=list)
    failed_tools: list[str] = Field(default_factory=list)
    tool_results: dict[str, list[dict]] = Field(default_factory=dict)
    tool_execution_records: list[ToolExecutionRecord] = Field(default_factory=list)
    market_data_timestamp: datetime | None = None
    regime_loaded: bool = False
    memory_loaded: bool = False
    strategy_memory_loaded: bool = False
    decision_memory_loaded: bool = False
    user_preference_loaded: bool = False
    contract_violations: list[str] = Field(default_factory=list)

    def record_tool_result(self, name: str, result: dict, call_id: str | None = None) -> None:
        self.called_tools.append(name)
        self.tool_results.setdefault(name, []).append(result)
        success = is_tool_result_success(result)
        error = result.get("error") if isinstance(result, dict) else None
        error_code = error.get("code") if isinstance(error, dict) else None
        error_message = error.get("message") if isinstance(error, dict) else (str(error) if error else None)
        self.tool_execution_records.append(ToolExecutionRecord(name=name, call_id=call_id, success=success, error_code=error_code, error_message=error_message))
        target = self.successful_tools if success else self.failed_tools
        if name not in target:
            target.append(name)
        if name == "get_market_regime" and success:
            self.regime_loaded = True
        if success and name in {"search_memory", "search_strategy_memory", "search_decision_memory", "search_user_preferences"} and _has_memory_results(result):
            self.memory_loaded = True
            self.strategy_memory_loaded |= name in {"search_memory", "search_strategy_memory"}
            self.decision_memory_loaded |= name in {"search_memory", "search_decision_memory"}
            self.user_preference_loaded |= name in {"search_memory", "search_user_preferences"}
        if name in {"get_market_snapshot", "get_kline"} and success:
            snapshot = result.get("snapshot", result)
            self.market_data_timestamp = _parse_timestamp(snapshot.get("as_of") or snapshot.get("snapshot_time") or snapshot.get("data_timestamp") or snapshot.get("timestamp"))


def is_tool_result_success(result: object) -> bool:
    if result is None:
        return False
    if isinstance(result, dict):
        return not (result.get("error") or result.get("success") is False or str(result.get("status", "")).lower() in {"failed", "error"})
    return True


def _has_memory_results(result: object) -> bool:
    if not isinstance(result, dict):
        return False
    for key in ("memories", "contexts", "results"):
        if isinstance(result.get(key), list) and result[key]:
            return True
    return False


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


class SkillContractViolation(Exception):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


class SkillContractValidator:
    def validate(self, skill: "SkillDefinition", state: SkillExecutionState, final_text: str, now: datetime | None = None) -> list[str]:
        execution = skill.execution
        called = set(state.called_tools)
        successful = set(state.successful_tools)
        violations: list[str] = []
        missing = [name for name in execution.required_tools if name not in successful]
        if missing:
            failed = [name for name in missing if name in state.failed_tools]
            unresolved = [name for name in missing if name not in state.failed_tools]
            violations.extend(f"REQUIRED_TOOL_FAILED:{name}" for name in failed)
            violations.extend(f"MISSING_REQUIRED_TOOL:{name}" for name in unresolved)
        forbidden = sorted(called.intersection(execution.forbidden_tools))
        if forbidden:
            violations.append(f"Forbidden tools were called: {', '.join(forbidden)}.")
        if state.round_index < execution.min_tool_rounds:
            violations.append(f"At least {execution.min_tool_rounds} tool rounds are required.")
        if execution.require_fresh_market_data:
            violations.extend(self._freshness_violations(state.market_data_timestamp, execution.freshness, now))
        if execution.require_memory_lookup and not state.memory_loaded:
            violations.append("MEMORY_LOOKUP_REQUIRED: call a dedicated search_memory tool and obtain at least one memory result.")
        if execution.require_regime and not state.regime_loaded:
            violations.append("Market regime is required; call get_market_regime.")
        for section in skill.output.required_sections:
            if f"### {section}" not in final_text and f"## {section}" not in final_text:
                violations.append(f"Final report is missing required section: {section}.")
        state.contract_violations = violations
        return violations

    @staticmethod
    def _freshness_violations(timestamp: datetime | None, policy: FreshnessPolicy | None, now: datetime | None) -> list[str]:
        if timestamp is None:
            return ["MARKET_DATA_TIMESTAMP_MISSING"]
        policy = policy or FreshnessPolicy()
        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        clock = MarketClock()
        timestamp_cn = clock.localize(timestamp)
        reference_cn = clock.localize(reference)
        age_minutes = (reference_cn - timestamp_cn).total_seconds() / 60
        if age_minutes < -5:
            return ["MARKET_DATA_TIMESTAMP_INVALID"]
        if policy.max_age_minutes is not None and age_minutes > policy.max_age_minutes:
            return ["MARKET_DATA_STALE"]
        try:
            timestamp_session = clock.trading_session(timestamp_cn)
            reference_session = clock.trading_session(reference_cn)
        except Exception:
            # Contract validation also runs in offline/unit-test contexts before the
            # calendar table has been migrated.  Preserve China-time semantics with
            # the calendar's documented weekday fallback instead of failing the
            # entire agent run because the optional calendar cache is unavailable.
            timestamp_session = SkillContractValidator._weekday_session(timestamp_cn)
            reference_session = SkillContractValidator._weekday_session(reference_cn)
        if policy.require_same_trading_day and timestamp_session != reference_session:
            return ["MARKET_DATA_NOT_SAME_TRADING_DAY"]
        if policy.require_after_market_open and timestamp_cn < datetime.combine(timestamp_session, time(9, 30), tzinfo=CN_TZ):
            return ["MARKET_DATA_BEFORE_MARKET_OPEN"]
        return []

    @staticmethod
    def _weekday_session(value: datetime) -> datetime.date:
        day = value.date()
        while day.weekday() >= 5:
            day = day.fromordinal(day.toordinal() - 1)
        return day


# Imported only for type checking at runtime-free module initialization.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.skill_loader import SkillDefinition

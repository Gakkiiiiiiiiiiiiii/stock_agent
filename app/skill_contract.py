from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from engines.market import trading_clock as _trading_clock

# Compatibility exports for validators and integrations.  The calendar alias
# deliberately points at the retained QMT-free business-clock implementation.
ExchangeTradingCalendar = _trading_clock.ExchangeTradingCalendar
TradingClock = _trading_clock.TradingClock
get_default_clock = _trading_clock.get_default_clock
CalendarUnavailable = _trading_clock.CalendarUnavailable


CN_TZ = ZoneInfo("Asia/Shanghai")


class SkillOutputContract(BaseModel):
    required_sections: list[str] = Field(default_factory=list)
    json_schema_name: str | None = None
    proposal_schema: str | None = None


class SkillGovernanceContract(BaseModel):
    require_risk: bool = False
    require_policy: bool = False


class SkillV3Contract(BaseModel):
    required_evidence: list[str] = Field(default_factory=list)
    required_specialists: list[str] = Field(default_factory=list)
    governance: SkillGovernanceContract = Field(default_factory=SkillGovernanceContract)
    freshness: dict[str, FreshnessPolicy] = Field(default_factory=dict)


class ConditionalRequirement(BaseModel):
    """Tools that become required only when a runtime query flag is truthy."""

    when: str
    require: list[str] = Field(default_factory=list)
    require_any: list[str] = Field(default_factory=list)


class SkillExecutionContract(BaseModel):
    required_tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    # Contract v2: named groups; at least one tool per group must succeed.
    required_any: dict[str, list[str]] = Field(default_factory=dict)
    # Contract v2: extra requirements activated by SkillExecutionState.query_flags.
    conditional_requirements: list[ConditionalRequirement] = Field(default_factory=list)
    max_tool_rounds: int = 8
    min_tool_rounds: int = 0
    require_fresh_market_data: bool = False
    require_memory_lookup: bool = False
    require_regime: bool = False
    freshness: FreshnessPolicy | None = None


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
    # Runtime flags that activate execution.conditional_requirements entries.
    query_flags: dict[str, bool] = Field(default_factory=dict)
    evidence_types: list[str] = Field(default_factory=list)
    specialists_completed: list[str] = Field(default_factory=list)
    risk_governed: bool = False
    policy_governed: bool = False

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
        if name in {"get_market_snapshot", "get_technical_evidence"} and success:
            snapshot = result.get("snapshot", result)
            self.market_data_timestamp = _parse_timestamp(snapshot.get("as_of") or snapshot.get("snapshot_time") or snapshot.get("data_timestamp") or snapshot.get("timestamp"))
        if success and isinstance(result, dict):
            evidence_type = result.get("evidence_type")
            if evidence_type:
                self.record_evidence(str(evidence_type))
            for item in result.get("evidence", []) if isinstance(result.get("evidence"), list) else []:
                if isinstance(item, dict) and item.get("evidence_type"):
                    self.record_evidence(str(item["evidence_type"]))
            specialist = result.get("specialist") or result.get("role")
            if specialist:
                self.record_specialist(str(specialist))
            if _validated_governance(result.get("risk_assessment"), "risk"):
                self.risk_governed = True
            if _validated_governance(result.get("policy_evaluation"), "policy"):
                self.policy_governed = True

    def record_evidence(self, evidence_type: str) -> None:
        if evidence_type not in self.evidence_types:
            self.evidence_types.append(evidence_type)

    def record_specialist(self, specialist: str) -> None:
        if specialist not in self.specialists_completed:
            self.specialists_completed.append(specialist)

    def record_bundle(self, bundle, artifacts=(), *, risk_assessment=None, policy_evaluation=None) -> None:
        """Populate v3 state from validated runtime outputs, not tool names."""
        for item in bundle.evidence:
            self.record_evidence(item.evidence_type.value)
            if item.evidence_type.value in {"MARKET_SNAPSHOT", "MARKET_REGIME", "SECTOR_STRENGTH"} and self.market_data_timestamp is None:
                self.market_data_timestamp = item.as_of
        for artifact in artifacts:
            role = getattr(artifact, "specialist", None) or getattr(artifact, "agent", None)
            status = str(getattr(artifact, "status", ""))
            if role is not None and status.upper().split(".")[-1] not in {"FAILED", "BLOCKED"}:
                self.record_specialist(getattr(role, "value", role))
        self.risk_governed = _validated_governance(risk_assessment, "risk")
        self.policy_governed = _validated_governance(policy_evaluation, "policy")


def is_tool_result_success(result: object) -> bool:
    if result is None:
        return False
    if isinstance(result, dict):
        return not (result.get("error") or result.get("success") is False or str(result.get("status", "")).lower() in {"failed", "error"})
    return True


def _validated_governance(value: object, kind: str) -> bool:
    if value is None:
        return False
    payload = value if isinstance(value, dict) else value.model_dump(mode="json") if hasattr(value, "model_dump") else None
    if not isinstance(payload, dict):
        return False
    if kind == "risk":
        return bool(payload.get("status") or payload.get("risk_level") or payload.get("veto") is not None)
    return bool(payload.get("status") or payload.get("decision") or payload.get("allowed") is not None)


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
        raw_value = str(value)
        if raw_value.endswith("Z"):
            raw_value = f"{raw_value[:-1]}+00:00"
        parsed = datetime.fromisoformat(raw_value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


class SkillContractViolation(Exception):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


class SkillContractValidator:
    def __init__(self, clock: TradingClock | None = None) -> None:
        self.clock = clock or get_default_clock()

    def validate(self, skill: SkillDefinition, state: SkillExecutionState, final_text: str, now: datetime | None = None) -> list[str]:
        execution = skill.execution
        called = set(state.called_tools)
        successful = set(state.successful_tools)
        violations: list[str] = []
        if getattr(skill, "version", 1) >= 3:
            required_evidence = set(getattr(skill, "required_evidence", []))
            missing_evidence = sorted(required_evidence - set(state.evidence_types))
            violations.extend(f"MISSING_REQUIRED_EVIDENCE:{name}" for name in missing_evidence)
            missing_specialists = sorted(set(getattr(skill, "required_specialists", [])) - set(state.specialists_completed))
            violations.extend(f"MISSING_REQUIRED_SPECIALIST:{name}" for name in missing_specialists)
            governance = getattr(skill, "governance", SkillGovernanceContract())
            if governance.require_risk and not state.risk_governed:
                violations.append("RISK_GOVERNANCE_REQUIRED")
            if governance.require_policy and not state.policy_governed:
                violations.append("POLICY_GOVERNANCE_REQUIRED")
        v3 = getattr(skill, "version", 1) >= 3
        missing = [] if v3 else [name for name in execution.required_tools if name not in successful]
        if missing:
            failed = [name for name in missing if name in state.failed_tools]
            unresolved = [name for name in missing if name not in state.failed_tools]
            violations.extend(f"REQUIRED_TOOL_FAILED:{name}" for name in failed)
            violations.extend(f"MISSING_REQUIRED_TOOL:{name}" for name in unresolved)
        forbidden = sorted(called.intersection(execution.forbidden_tools))
        if forbidden:
            violations.append(f"Forbidden tools were called: {', '.join(forbidden)}.")
        for group, tools in ({} if v3 else execution.required_any).items():
            if not any(name in successful for name in tools):
                violations.append(f"MISSING_REQUIRED_ANY:{group} (at least one of: {', '.join(tools)})")
        for condition in ([] if v3 else execution.conditional_requirements):
            if not state.query_flags.get(condition.when):
                continue
            for name in condition.require:
                if name not in successful:
                    violations.append(f"MISSING_CONDITIONAL_TOOL:{condition.when}:{name}")
            if condition.require_any and not any(name in successful for name in condition.require_any):
                violations.append(f"MISSING_CONDITIONAL_ANY:{condition.when} (at least one of: {', '.join(condition.require_any)})")
        if state.round_index < execution.min_tool_rounds:
            violations.append(f"At least {execution.min_tool_rounds} tool rounds are required.")
        if execution.require_fresh_market_data and not v3:
            violations.extend(self._freshness_violations(state.market_data_timestamp, execution.freshness, now, self.clock))
        if execution.require_memory_lookup and not v3 and not state.memory_loaded:
            violations.append("MEMORY_LOOKUP_REQUIRED: call a dedicated search_memory tool and obtain at least one memory result.")
        if execution.require_regime and not v3 and not state.regime_loaded:
            violations.append("Market regime is required; call get_market_regime.")
        if v3:
            market_policy = (getattr(skill, "freshness", {}) or {}).get("market")
            if market_policy is not None:
                violations.extend(self._freshness_violations(state.market_data_timestamp, market_policy, now, self.clock))
        for section in skill.output.required_sections:
            if f"### {section}" not in final_text and f"## {section}" not in final_text:
                violations.append(f"Final report is missing required section: {section}.")
        state.contract_violations = violations
        return violations

    @staticmethod
    def _freshness_violations(timestamp: datetime | None, policy: FreshnessPolicy | None, now: datetime | None, clock: TradingClock | None = None) -> list[str]:
        if timestamp is None:
            return ["MARKET_DATA_TIMESTAMP_MISSING"]
        policy = policy or FreshnessPolicy()
        clock = clock or get_default_clock()
        reference = now or clock.now("CN_A")
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
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
        except CalendarUnavailable:
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

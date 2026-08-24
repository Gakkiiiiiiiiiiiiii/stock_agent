from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts.immutable import ensure_json, freeze


def resolve_conflicts(signal_score: float, portfolio_ok: bool, retrieved_warnings: list[str] | None = None) -> dict:
    warnings = list(retrieved_warnings or [])
    final_action = "observe"
    if signal_score >= 80 and portfolio_ok:
        final_action = "candidate_buy"
    elif signal_score >= 65 and portfolio_ok:
        final_action = "wait_confirmation"
    else:
        warnings.append("信号或组合约束不足")
    return {"final_action": final_action, "warnings": warnings}


# 详细修改方案 §11：Conflict Resolver v2 冲突类型与领域权威。
CONFLICT_TYPES = ("FACT_CONFLICT", "REGIME_CONFLICT", "SIGNAL_CONFLICT", "PORTFOLIO_CONFLICT", "RISK_CONFLICT")

DOMAIN_AUTHORITY = {
    "FACT_CONFLICT": "EVIDENCE_AUTHORITY",
    "REGIME_CONFLICT": "MARKET_SPECIALIST",
    "SIGNAL_CONFLICT": "FACTOR_SPECIALIST",
    "RISK_CONFLICT": "RISK_SPECIALIST",
    "PORTFOLIO_CONFLICT": "PORTFOLIO_SPECIALIST",
}

DOMAIN_AGENT_ALIASES = {
    "EVIDENCE_AUTHORITY": {"EVIDENCE_AUTHORITY", "EVIDENCEAUTHORITY", "RESEARCHAGENT", "RESEARCH"},
    "MARKET_SPECIALIST": {"MARKET_SPECIALIST", "MARKETAGENT", "MARKET"},
    "FACTOR_SPECIALIST": {"FACTOR_SPECIALIST", "FACTORAGENT", "FACTOR"},
    "RISK_SPECIALIST": {"RISK_SPECIALIST", "RISKAGENT", "RISK"},
    "PORTFOLIO_SPECIALIST": {"PORTFOLIO_SPECIALIST", "PORTFOLIOAGENT", "PORTFOLIO"},
}


def _agent_key(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).replace("_", "").replace("-", "").replace(" ", "").upper()


def _is_domain_owner(value: Any, authority: str) -> bool:
    return _agent_key(value) in {_agent_key(alias) for alias in DOMAIN_AGENT_ALIASES.get(authority, {authority})}


def _is_risk_agent(value: Any) -> bool:
    return _agent_key(value) in {_agent_key(alias) for alias in DOMAIN_AGENT_ALIASES["RISK_SPECIALIST"]}


class ConflictOpinion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    value: Any
    confidence: float = Field(default=0.0, ge=0, le=1)
    veto: bool = False

    @field_validator("value", mode="after")
    @classmethod
    def freeze_value(cls, value: Any) -> Any:
        ensure_json(value)
        return freeze(value)


class DecisionConflict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conflict_id: str
    dimension: str
    conflict_type: str
    opinions: list[ConflictOpinion]
    resolution_policy: str
    domain_owner: str | None = None
    resolved_value: Any | None = None
    resolved_by: str | None = None
    risk_veto: bool = False

    @field_validator("opinions")
    @classmethod
    def freeze_opinions(cls, value: list[ConflictOpinion]) -> list[ConflictOpinion]:
        return freeze(value)

    @field_validator("resolved_value", mode="after")
    @classmethod
    def freeze_resolved_value(cls, value: Any) -> Any:
        if value is None:
            return None
        ensure_json(value)
        return freeze(value)


def resolve_conflicts_v2(conflicts: list[dict]) -> dict:
    """§11：按领域权威解析冲突；Risk 拥有 VETO 权（不是仅“意见之一”）。

    每条 conflict：{type, dimension, options: [{agent, value}], risk_veto?: bool}
    """
    resolutions: list[dict] = []
    vetoed = False
    veto_reasons: list[str] = []
    for raw_conflict in conflicts or []:
        conflict = raw_conflict.model_dump() if isinstance(raw_conflict, DecisionConflict) else raw_conflict
        conflict_type = conflict.get("type") or conflict.get("conflict_type") or "SIGNAL_CONFLICT"
        authority = DOMAIN_AUTHORITY.get(conflict_type, "SUPERVISOR")
        options = conflict.get("options") or conflict.get("opinions") or []
        options = [item.model_dump() if isinstance(item, ConflictOpinion) else item for item in options if isinstance(item, (dict, ConflictOpinion))]
        owner = next((item for item in options if _is_domain_owner(item.get("agent"), authority)), None)
        policy = next((item for item in options if _agent_key(item.get("agent")) in {"POLICYENGINE", "DETERMINISTICPOLICY"}), None)
        confidence_fallback = max(options, key=lambda item: (float(item.get("confidence", 0.0)), str(item.get("agent", "")), str(item.get("value", ""))), default={})
        risk_veto_opinion = next((item for item in options if _is_risk_agent(item.get("agent")) and item.get("veto")), None)
        selected = risk_veto_opinion or owner or policy or confidence_fallback
        resolved_value = selected.get("value")
        if conflict.get("risk_veto") or risk_veto_opinion or (conflict_type == "RISK_CONFLICT" and (owner or {}).get("veto")):
            vetoed = True
            veto_reasons.append(str(conflict.get("dimension") or "risk"))
        resolutions.append(
            {
                "dimension": conflict.get("dimension"),
                "type": conflict_type,
                "resolved_by": selected.get("agent") or authority,
                "resolved_value": resolved_value,
            }
        )
    final_action = "veto" if vetoed else "proceed"
    return {
        "final_action": final_action,
        "vetoed": vetoed,
        "veto_reasons": veto_reasons,
        "resolutions": resolutions,
    }


__all__ = ["resolve_conflicts", "resolve_conflicts_v2", "CONFLICT_TYPES", "DOMAIN_AUTHORITY", "ConflictOpinion", "DecisionConflict"]

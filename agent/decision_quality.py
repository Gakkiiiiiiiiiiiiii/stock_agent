"""决策质量评估（设计文档 §62 / §63 / §90）。

依赖服务不可用时决策不得静默降级为零值证据，必须显式标记 DEGRADED。
"""
from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from contracts.immutable import freeze

UNAVAILABLE_SIGNALS = {
    "FACTOR_UNAVAILABLE",
    "CONTENT_UNAVAILABLE",
    "MARKET_DATA_UNAVAILABLE",
    "QUANT_UNAVAILABLE",
}

SEVERE_SIGNALS = UNAVAILABLE_SIGNALS | {"FACTOR_UNIVERSE_NOT_PROVIDED"}


class DecisionQuality(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    DEGRADED = "DEGRADED"


class DecisionQualityAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)
    level: DecisionQuality
    evidence_coverage: float = Field(default=0.0, ge=0, le=1)
    domain_coverage: dict[str, float] = Field(default_factory=dict)
    dependency_health_score: float = Field(default=1.0, ge=0, le=1)
    freshness_score: float = Field(default=1.0, ge=0, le=1)
    specialist_completion_score: float = Field(default=1.0, ge=0, le=1)
    conflict_score: float = Field(default=0.0, ge=0, le=1)
    unknowns: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    actionable: bool = False
    veto_context: bool = False

    @model_validator(mode="after")
    def freeze_detail(self):
        object.__setattr__(self, "domain_coverage", freeze(self.domain_coverage))
        object.__setattr__(self, "unknowns", freeze(self.unknowns))
        object.__setattr__(self, "degraded_reasons", freeze(self.degraded_reasons))
        return self

    def __getitem__(self, key):
        return self.level.value if key == "level" else getattr(self, key)


def compute_decision_quality_v2(*, coverage: dict[str, float] | None = None, dependency_status: list[dict] | None = None, specialist_artifacts: list[dict] | None = None, target_weight_requested: bool = False, evidence_quality: list[str] | None = None, evidence: list | None = None, decision_time=None, conflicts: list | None = None) -> DecisionQualityAssessment:
    coverage = coverage or {}
    def as_dict(item):
        if isinstance(item, dict):
            return item
        return item.model_dump(mode="json") if hasattr(item, "model_dump") else {}
    statuses = [as_dict(item) for item in dependency_status or []]
    artifacts = [as_dict(item) for item in specialist_artifacts or []]
    if evidence and evidence_quality is None:
        evidence_quality = [str(getattr(item, "quality_status", "")).split(".")[-1].upper() for item in evidence]
    unknowns = {str(item) for artifact in artifacts for item in artifact.get("unknowns") or []}
    unavailable = {str(item.get("status", "")).split(".")[-1].upper() for item in statuses}
    reasons: set[str] = set()
    market_dependency_bad = any(str(item.get("system", "")).lower().split(".")[-1] in {"quant", "market"} and str(item.get("status", "")).split(".")[-1].upper() in {"UNAVAILABLE", "STALE"} for item in statuses)
    if coverage.get("market", 0) < 1 or market_dependency_bad:
        reasons.add("CRITICAL_MARKET_MISSING")
    portfolio_missing = target_weight_requested and coverage.get("portfolio", 0) < 1
    if portfolio_missing:
        reasons.add("INSUFFICIENT_CONTEXT")
    risk_failed = "RISK_FAILED" in unknowns or any(str(item.get("status", "")).split(".")[-1].upper() == "FAILED" and str(item.get("specialist", item.get("agent", ""))).upper().endswith("RISK") for item in artifacts)
    if risk_failed:
        reasons.add("RISK_FAILED")
    if evidence_quality and all(str(value).upper() in {"UNVERIFIED", "REJECTED"} for value in evidence_quality):
        reasons.add("ALL_SUPPORTING_EVIDENCE_UNVERIFIED")
    degraded = bool(reasons) or bool(unavailable & {"UNAVAILABLE", "STALE", "DEGRADED"}) or bool(unknowns)
    veto = risk_failed or portfolio_missing
    actionable = not veto and not reasons.intersection({"CRITICAL_MARKET_MISSING", "ALL_SUPPORTING_EVIDENCE_UNVERIFIED"})
    if reasons.intersection({"CRITICAL_MARKET_MISSING", "RISK_FAILED", "INSUFFICIENT_CONTEXT"}):
        level = DecisionQuality.DEGRADED
    elif "ALL_SUPPORTING_EVIDENCE_UNVERIFIED" in reasons:
        level = DecisionQuality.LOW
    elif degraded:
        level = DecisionQuality.DEGRADED
    elif actionable and min(coverage.values() or [0]) >= 1:
        level = DecisionQuality.HIGH
    else:
        level = DecisionQuality.MEDIUM
    dep_score = 0.0 if "UNAVAILABLE" in unavailable else 0.5 if unavailable & {"STALE", "DEGRADED"} else 1.0
    evidence_score = sum(coverage.values()) / len(coverage) if coverage else 0.0
    quality_score = 0.0 if evidence_quality and all(str(value).upper() in {"UNVERIFIED", "REJECTED"} for value in evidence_quality) else 1.0
    if evidence:
        quality_values = evidence_quality or []
        if decision_time is not None:
            ages = [(decision_time - item.as_of).total_seconds() for item in evidence if getattr(item, "as_of", None) is not None]
            fresh_score = max(0.0, min(1.0, 1.0 - (sum(max(age, 0) for age in ages) / len(ages)) / 86400)) if ages else 0.0
        else:
            fresh_score = 0.5 if "STALE" in quality_values else 1.0
    else:
        fresh_score = 0.5 if "STALE" in unavailable else 1.0
    required_roles = {"MARKET", "RESEARCH", "TECHNICAL", "FACTOR", "PORTFOLIO", "RISK"}
    completed_roles = {str(item.get("specialist", item.get("agent", ""))).split(".")[-1].upper().replace("AGENT", "") for item in artifacts if str(item.get("status", "")).split(".")[-1].upper() in {"SUCCESS", "DEGRADED", "COMPLETED"}}
    completion_score = len(required_roles & completed_roles) / len(required_roles)
    conflict_count = len(conflicts or [])
    conflict_score = max(0.0, 1.0 - min(1.0, conflict_count / max(1, len(coverage) or 1)))
    return DecisionQualityAssessment(level=level, evidence_coverage=evidence_score * quality_score, domain_coverage=coverage, dependency_health_score=dep_score, freshness_score=fresh_score, specialist_completion_score=completion_score, conflict_score=conflict_score, unknowns=sorted(unknowns), degraded_reasons=sorted(reasons), actionable=actionable, veto_context=veto)


def compute_decision_quality(artifacts: list[dict], errors: list[dict] | None = None) -> str:
    """根据 Specialist artifacts 计算 HIGH / MEDIUM / LOW / DEGRADED。"""
    warnings: set[str] = set()
    roles_with_evidence: set[str] = set()
    for artifact in artifacts:
        warnings.update(str(item) for item in artifact.get("warnings") or [])
        if artifact.get("confidence", 0) > 0 and not artifact.get("warnings"):
            roles_with_evidence.add(str(artifact.get("agent")))
    if warnings & SEVERE_SIGNALS:
        return "DEGRADED"
    if errors:
        return "LOW"
    core_roles = {"MarketAgent", "ResearchAgent", "TechnicalAgent", "FactorAgent", "RiskAgent"}
    if core_roles <= roles_with_evidence:
        return "HIGH"
    if len(roles_with_evidence) >= 2:
        return "MEDIUM"
    return "LOW"


__all__ = ["DecisionQuality", "DecisionQualityAssessment", "UNAVAILABLE_SIGNALS", "compute_decision_quality", "compute_decision_quality_v2"]

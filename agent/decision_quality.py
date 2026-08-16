"""决策质量评估（设计文档 §62 / §63 / §90）。

依赖服务不可用时决策不得静默降级为零值证据，必须显式标记 DEGRADED。
"""
from __future__ import annotations

UNAVAILABLE_SIGNALS = {
    "FACTOR_UNAVAILABLE",
    "CONTENT_UNAVAILABLE",
    "MARKET_DATA_UNAVAILABLE",
    "QUANT_UNAVAILABLE",
}

SEVERE_SIGNALS = UNAVAILABLE_SIGNALS | {"FACTOR_UNIVERSE_NOT_PROVIDED"}


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


__all__ = ["UNAVAILABLE_SIGNALS", "compute_decision_quality"]

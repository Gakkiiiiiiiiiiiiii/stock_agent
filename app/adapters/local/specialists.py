"""Local bundle-only specialist adapter.

Concrete agent graph integrations are kept outside the application layer.
"""
from __future__ import annotations

from typing import Any

from agent.contracts import AgentRole, SpecialistArtifact, SpecialistStatus, ToolUsage
from agent.plans.daily_market_decision import build_daily_market_decision_graph
from agent.specialists import (
    FactorSpecialist,
    MarketSpecialist,
    PortfolioSpecialist,
    ResearchSpecialist,
    RiskSpecialist,
    TechnicalSpecialist,
)
from agent.supervisor import Supervisor
from app.skill_loader import load_skills


class SpecialistRunnerComponent:
    """Run specialists against the immutable bundle only."""

    class BundleToolRegistry:
        def __init__(self, bundle: Any) -> None:
            self.bundle = bundle

        def _items(self, prefix: tuple[str, ...]) -> list[dict]:
            return [{**dict(item.payload), "evidence_id": item.evidence_id} for item in self.bundle.evidence if item.evidence_type.value in prefix]

        def execute(self, name: str, payload: dict | None = None) -> dict:
            payload = payload or {}
            symbol = payload.get("symbol")
            if name == "get_market_features":
                return (self._items(("MARKET_SNAPSHOT", "TECHNICAL_PROFILE", "TECHNICAL_SIGNAL")) or [{}])[0]
            if name == "get_market_regime":
                return (self._items(("MARKET_REGIME",)) or [{}])[0]
            if name == "get_sector_strength":
                return {"items": self._items(("SECTOR_STRENGTH",))}
            if name == "get_technical_evidence":
                values = self._items(("TECHNICAL_SIGNAL", "TECHNICAL_PROFILE"))
                return next((item for item in values if not symbol or item.get("symbol") == symbol), {})
            if name == "retrieve_relevant_context":
                return {"items": [{**dict(item.payload), "evidence_id": item.evidence_id} for item in self.bundle.evidence]}
            if name == "construct_portfolio_v2":
                values = self._items(("PORTFOLIO_POSITION", "PORTFOLIO_EXPOSURE", "PORTFOLIO_RISK"))
                return values[0] if values else {}
            if name == "get_portfolio_risk_inputs":
                return (self._items(("PORTFOLIO_RISK",)) or [{}])[0]
            raise ValueError(f"bundle-only registry rejected tool: {name}")

    class BundleFactorClient:
        def __init__(self, bundle: Any) -> None:
            self.bundle = bundle

        def score_alpha(self, request: Any) -> dict:
            scores = [{**dict(item.payload), "evidence_id": item.evidence_id} for item in self.bundle.evidence if item.evidence_type.value in {"FACTOR_SCORE", "FACTOR_SET", "FACTOR_RESEARCH_RESULT"}]
            return {"scores": scores, "factor_set_version": next((item.data_version for item in self.bundle.evidence if item.evidence_type.value == "FACTOR_SET"), None), "as_of": request.as_of}

    def run(self, *, task_type: str, objective: str, context: dict[str, Any], decision_time: Any, bundle: Any) -> list[Any]:
        registry = self.BundleToolRegistry(bundle)
        specialist_context = {
            **self._safe_context(context), "bundle_id": bundle.bundle_id, "bundle_hash": bundle.bundle_hash,
            "evidence_refs": [item.evidence_id for item in bundle.evidence],
            "candidate_symbols": list(context.get("subjects") or []), "symbols": list(context.get("subjects") or []),
            "universe": list(context.get("subjects") or []), "candidates": list(context.get("subjects") or []),
        }
        specialists = {
            AgentRole.MARKET: MarketSpecialist(registry, specialist_context),
            AgentRole.RESEARCH: ResearchSpecialist(registry, specialist_context),
            AgentRole.TECHNICAL: TechnicalSpecialist(registry, specialist_context),
            AgentRole.FACTOR: FactorSpecialist(registry, specialist_context, factor_client=self.BundleFactorClient(bundle)),
            AgentRole.PORTFOLIO: PortfolioSpecialist(registry, specialist_context),
            AgentRole.RISK: RiskSpecialist(registry, specialist_context),
        }
        graph = build_daily_market_decision_graph(objective, tool_budget=5, token_budget=1000)
        active_skill = next((item for item in load_skills() if item.slug == str(context.get("skill") or task_type)), None)
        required_roles = {str(item).upper() for item in (active_skill.required_specialists if active_skill else [])}
        if required_roles:
            keep = {task_id for task_id, task in graph.tasks.items() if task.assigned_agent and (task.assigned_agent.name in required_roles or task.assigned_agent.value.upper() in required_roles)}
            for task_id in list(graph.tasks):
                if task_id not in keep:
                    graph.tasks.pop(task_id, None)
                    graph._dependencies.pop(task_id, None)
            for dependencies in graph._dependencies.values():
                dependencies.intersection_update(keep)
        for task in graph.tasks.values():
            task.task_type = task_type
            task.as_of = decision_time
        result = Supervisor(specialists).run(graph)
        by_role = {str(item.get("specialist") or item.get("agent")): item for item in result.get("artifacts") or []}
        roles = (AgentRole.MARKET, AgentRole.RESEARCH, AgentRole.TECHNICAL, AgentRole.FACTOR, AgentRole.PORTFOLIO, AgentRole.RISK)
        if required_roles:
            roles = tuple(role for role in roles if role.name in required_roles or role.value.upper() in required_roles)
        artifacts: list[Any] = []
        for role in roles:
            item = by_role.get(role.name) or by_role.get(role.value)
            if item is not None:
                artifacts.append(self._normalize(SpecialistArtifact.model_validate(item), bundle))
                continue
            task = next((value for value in graph.tasks.values() if value.assigned_agent == role), None)
            artifacts.append(SpecialistArtifact(task_id=task.task_id if task else f"{role.value.lower()}-missing", specialist=role, status=SpecialistStatus.FAILED, conclusion={}, warnings=["SPECIALIST_FAILED"], unknowns=["SPECIALIST_FAILED"], confidence=0.0, tool_usage=ToolUsage(calls=0)))
        return artifacts

    @staticmethod
    def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
        allowed = {"skill", "account_id", "mode", "analysis_type", "subjects", "trace_context"}
        return {key: context[key] for key in allowed if key in context}

    @staticmethod
    def _normalize(artifact: Any, bundle: Any) -> Any:
        required = {
            AgentRole.MARKET: {"MARKET_SNAPSHOT", "MARKET_REGIME", "SECTOR_STRENGTH"},
            AgentRole.RESEARCH: {"KNOWLEDGE_CLAIM", "CATALYST", "RISK_EVENT", "VALUATION_FACT", "EARNINGS_FACT"},
            AgentRole.TECHNICAL: {"TECHNICAL_SIGNAL", "TECHNICAL_PROFILE"},
            AgentRole.FACTOR: {"FACTOR_SCORE", "FACTOR_SET", "FACTOR_RESEARCH_RESULT"},
            AgentRole.PORTFOLIO: {"PORTFOLIO_POSITION", "PORTFOLIO_EXPOSURE", "PORTFOLIO_RISK"},
            AgentRole.RISK: {"PORTFOLIO_RISK", "RISK_EVENT"},
        }[AgentRole[artifact.specialist.value]]
        refs = [item.evidence_id for item in bundle.evidence if item.evidence_type.value in required]
        values = artifact.model_dump(mode="python")
        values.pop("artifact_hash", None)
        values["evidence_refs"] = refs
        if not refs:
            if artifact.status != SpecialistStatus.FAILED:
                values["status"] = SpecialistStatus.DEGRADED
            values["warnings"] = sorted(set(values.get("warnings") or []) | {f"{artifact.specialist.value}_EVIDENCE_MISSING"})
            values["unknowns"] = sorted(set(values.get("unknowns") or []) | {f"{artifact.specialist.value}_EVIDENCE_MISSING"})
            values["confidence"] = min(float(values.get("confidence") or 0.0), 0.4)
            values["conclusion"] = {"unknowns": values["unknowns"]}
        return SpecialistArtifact.model_validate(values)

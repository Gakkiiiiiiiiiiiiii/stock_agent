"""Application service for compatibility/narrative analysis endpoints.

This service deliberately owns the historical analysis pipeline.  It never
creates a formal decision; the v2 decision calculator is a separate component.
The constructor receives the small set of policy, persistence, and clock ports
needed by the legacy adapter, keeping the public ``DecisionRuntime`` façade
free of analysis implementation details.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

DECISION_RUNTIME_VERSION = "decision-runtime.v1"
CONTENT_FACTOR_SIGNAL_VERSION = "content-factor-signal.v3"


class AnalysisApplicationService:
    """Run non-authoritative analysis with explicitly injected collaborators."""

    def __init__(self, *, mode_resolver: Callable[[], tuple[Any, str | None]], clock_now: Callable[[], datetime], policy_engine: Any, decision_service: Any, tool_results: Any, degraded_mode: Any, task_graph_type: Any, task_type_factory: Any, artifact_factory: Any, success_status: Any, supervisor_type: Any, proposal_type: Any, policy_context_type: Any, profile_type: Any, recommendation_type: Any, suitability_fn: Callable[..., Any], conflict_resolver: Callable[..., Any], runtime_segment_fn: Callable[..., Any]) -> None:
        self._mode_resolver = mode_resolver
        self._clock_now = clock_now
        self._policy_engine = policy_engine
        self._decision_service = decision_service
        self._tool_results = tool_results
        self._degraded_mode = degraded_mode
        self._task_graph_type = task_graph_type
        self._task_type_factory = task_type_factory
        self._artifact_factory = artifact_factory
        self._success_status = success_status
        self._supervisor_type = supervisor_type
        self._proposal_type = proposal_type
        self._policy_context_type = policy_context_type
        self._profile_type = profile_type
        self._recommendation_type = recommendation_type
        self._suitability_fn = suitability_fn
        self._conflict_resolver = conflict_resolver
        self._runtime_segment_fn = runtime_segment_fn

    @staticmethod
    def _report_text(report: Any) -> str:
        if isinstance(report, str) and report.strip():
            return report
        if isinstance(report, dict):
            text = report.get("summary") or report.get("text")
            if isinstance(text, str) and text.strip():
                return text
        return "Analysis produced no narrative output."

    def run_pipeline(self, *, task_type: str, role: Any, objective: str, execute: Callable[[], dict], query: str, subject: str | None, as_of: date | None = None, emit: Callable[[str, dict], None] | None = None) -> dict:
        mode, fallback_reason = self._mode_resolver()
        supervised = self._run_supervised(task_type=task_type, role=role, objective=objective, execute=execute, as_of=as_of)
        payload = supervised["payload"]
        governed = self._govern(payload)
        persisted = self._persist(objective=query, payload=payload, governed=governed, mode=mode, fallback_reason=fallback_reason, task_type=task_type, subject=subject, agent_run_id=supervised.get("agent_run_id"), decision_quality=supervised.get("decision_quality"))
        payload.update(self._actionable_segment(governed, persisted, mode))
        if emit:
            emit("done", payload)
        return payload

    def run_narrative(self, *, task_type: str, role: Any, objective: str, execute: Callable[[], dict], query: str, subject: str | None, as_of: date | None = None, emit: Callable[[str, dict], None] | None = None) -> dict:
        mode, fallback_reason = self._mode_resolver()
        try:
            raw = execute()
        except Exception as exc:  # noqa: BLE001 - compatibility boundary converts adapter failures to non-actionable text
            raw = {"report": f"Narrative adapter unavailable: {exc}"}
            mode = self._degraded_mode
            fallback_reason = type(exc).__name__
        payload = {"orchestration": "legacy-narrative-adapter", "runtime_mode": mode.value if hasattr(mode, "value") else str(mode), "fallback_reason": fallback_reason, "actionable": False, "report": self._report_text(raw.get("report") if isinstance(raw, dict) else raw), "task_type": task_type}
        if emit:
            emit("done", payload)
        return payload

    def _run_supervised(self, *, task_type: str, role: Any, objective: str, execute: Callable[[], dict], as_of: date | None) -> dict:
        as_of_dt = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC) if isinstance(as_of, date) else self._clock_now()
        graph = self._task_graph_type()
        graph.add_task(self._task_type_factory(task_type=task_type, assigned_agent=role, objective=objective, as_of=as_of_dt))

        def specialist(task: Any, _shared: Any) -> Any:
            adapter_payload = execute()
            proposal_payload = adapter_payload.get("proposal") if isinstance(adapter_payload, dict) else None
            confidence = float((proposal_payload or {}).get("confidence") or 0.0)
            return self._artifact_factory(agent=role, task_id=task.task_id, status=self._success_status, conclusion={"payload": adapter_payload}, confidence=max(0.0, min(confidence, 1.0)))

        result = self._supervisor_type({role: specialist}).run(graph)
        artifacts = result.get("artifacts") or []
        payload = ((artifacts[0] or {}).get("conclusion") or {}).get("payload") if artifacts else {}
        return {"payload": payload if isinstance(payload, dict) else {}, "agent_run_id": result.get("agent_run_id"), "decision_quality": result.get("decision_quality"), "errors": result.get("errors") or []}

    def _govern(self, payload: dict) -> dict:
        payload = payload if isinstance(payload, dict) else {}
        proposal_payload = dict(payload.get("proposal") or {})
        proposal = self._proposal_type(symbol=str(proposal_payload.get("symbol") or payload.get("symbol") or ""), action=str(proposal_payload.get("action") or "HOLD").upper(), proposed_weight=float(proposal_payload.get("proposed_weight") or 0.0), confidence=float(proposal_payload.get("confidence") or 0.0), thesis_refs=list(proposal_payload.get("thesis_refs") or []), sector=proposal_payload.get("sector") or payload.get("sector"), theme=proposal_payload.get("theme") or payload.get("theme"), evidence_count=int(proposal_payload.get("evidence_count") or 0), factor_coverage=float(proposal_payload.get("factor_coverage") or 1.0), liquidity_ok=bool(proposal_payload.get("liquidity_ok", True)), is_st=bool(proposal_payload.get("is_st", False)), is_suspended=bool(proposal_payload.get("is_suspended", False)))
        context = self._policy_context_type(portfolio_drawdown_mode=bool(payload.get("portfolio_drawdown_mode", False)), restricted_universe=list(payload.get("restricted_universe") or []), existing_weights={str(k): float(v) for k, v in (payload.get("existing_weights") or {}).items()})
        conflicts = [dict(item) for item in (payload.get("conflicts") or []) if isinstance(item, dict)]
        risk = dict(payload.get("risk") or {})
        if risk.get("veto"):
            conflicts.append({"type": "RISK_CONFLICT", "dimension": str(risk.get("reason") or "RISK_VETO"), "options": [{"agent": "RiskAgent", "value": "VETO", "veto": True}], "risk_veto": True})
        resolution = self._conflict_resolver(conflicts)
        decision = self._policy_engine.evaluate(proposal, context)
        vetoed = bool(resolution.get("vetoed"))
        suitability: dict | None = None
        profile_payload = payload.get("investor_profile")
        if isinstance(profile_payload, dict) and profile_payload:
            profile = self._profile_type(risk_level=str(profile_payload.get("risk_level") or "BALANCED"), investment_horizon_years=float(profile_payload.get("investment_horizon_years") or 3.0), liquidity_need=str(profile_payload.get("liquidity_need") or "MEDIUM"), max_drawdown_tolerance=float(profile_payload.get("max_drawdown_tolerance") or 0.15), allowed_markets=tuple(profile_payload.get("allowed_markets") or ("CN_A",)), allowed_products=tuple(profile_payload.get("allowed_products") or ("EQUITY",)))
            recommendation = self._recommendation_type(symbol=proposal.symbol or "UNKNOWN", action=proposal.action, weight=proposal.proposed_weight, market=str(profile_payload.get("market") or "CN_A"), product=str(profile_payload.get("product") or "EQUITY"), risk_rating=str(profile_payload.get("product_risk_rating") or "BALANCED"), expected_max_drawdown=float(profile_payload.get("expected_max_drawdown") or 0.10), holding_horizon_years=float(profile_payload.get("holding_horizon_years") or 1.0), liquidity_profile=str(profile_payload.get("product_liquidity_profile") or "HIGH"))
            suitability = self._suitability_fn(profile, recommendation)
        approved = bool(decision.approved) and not vetoed
        approved_weight = decision.approved_weight if approved else 0.0
        if vetoed:
            action = "VETO"
        elif not approved:
            action = "REJECT"
        elif suitability is not None and not suitability.get("suitable"):
            action, approved, approved_weight = "REJECT", False, 0.0
        else:
            action = proposal.action
            if suitability is not None:
                approved_weight = min(approved_weight, float(suitability.get("approved_weight") or approved_weight))
        return {"proposal": proposal, "policy_decision": decision, "resolution": resolution, "suitability": suitability, "final_decision": {"action": action, "approved": approved, "approved_weight": round(float(approved_weight), 6), "vetoed": vetoed, "veto_reasons": list(resolution.get("veto_reasons") or []), "rejections": list(decision.rejections), "adjustments": list(decision.adjustments), "suitability": suitability}}

    def _persist(self, *, objective: str, payload: dict, governed: dict, mode: Any, fallback_reason: str | None, task_type: str, subject: str | None, agent_run_id: str | None, decision_quality: str | None) -> dict:
        proposal, decision, final_decision = governed["proposal"], governed["policy_decision"], governed["final_decision"]
        market_snapshot_id = payload.get("market_snapshot_id")
        content_segment, content_snapshot_ids = self._content_lineage(payload)
        tool_segment = self._record_tool_result(task_type=task_type, objective=objective, payload=payload, agent_run_id=agent_run_id, snapshot_refs=([str(market_snapshot_id)] if market_snapshot_id else []) + content_snapshot_ids)
        snapshot_segments = {"market": {"snapshot_id": market_snapshot_id, "data_version": payload.get("market_data_version")}, "content": content_segment, "factor": {"factor_set_version": payload.get("factor_set_version"), "research_experiment_id": payload.get("research_experiment_id")}, "strategy": {"strategy_id": payload.get("selected_skill") or task_type, "strategy_version": payload.get("selected_skill") or task_type}, "runtime": self._runtime_segment_fn(mode, fallback_reason=fallback_reason, supervisor_version=DECISION_RUNTIME_VERSION), "proposal": proposal.to_dict(), "policy": {**decision.to_dict(), "risk_veto": final_decision["vetoed"], "veto_reasons": final_decision["veto_reasons"], "final_action": final_decision["action"]}, "tools": tool_segment, "inputs": {"market_snapshot_ids": [market_snapshot_id] if market_snapshot_id else [], "content_snapshot_ids": content_snapshot_ids, "research_experiment_ids": [payload["research_experiment_id"]] if payload.get("research_experiment_id") else [], "factor_set_ids": [payload["factor_set_version"]] if payload.get("factor_set_version") else []}, "output": {"final_decision": final_decision["action"], "approved_weight": final_decision["approved_weight"]}}
        candidates = [{"symbol": subject, "confidence": proposal.confidence}] if subject and proposal.symbol else []
        return self._decision_service.save_decision(query=objective, candidates=candidates, themes=[proposal.theme] if proposal.theme else [], sector=proposal.sector, market_regime=payload.get("market_regime"), agent_run_id=agent_run_id, supervisor_version=DECISION_RUNTIME_VERSION, participating_agents=[mode.value], decision_quality=decision_quality, decision_snapshot=snapshot_segments)

    @staticmethod
    def _content_lineage(payload: dict) -> tuple[dict, list[str]]:
        response = payload.get("content_signal_response")
        items = [item for item in (response.get("items") or []) if isinstance(item, dict)] if isinstance(response, dict) else []
        contract_version = str(response.get("contract_version") or CONTENT_FACTOR_SIGNAL_VERSION) if isinstance(response, dict) else CONTENT_FACTOR_SIGNAL_VERSION
        snapshot_ids = sorted({str(item.get("content_snapshot_id")) for item in items if item.get("content_snapshot_id")})
        segment = {"signal_contract": contract_version}
        if snapshot_ids:
            segment["snapshot_id"] = snapshot_ids[0]
        return segment, snapshot_ids

    def _record_tool_result(self, *, task_type: str, objective: str, payload: dict, agent_run_id: str | None, snapshot_refs: list[str]) -> dict:
        snapshot = self._tool_results.record(tool_id=f"decision_runtime.{task_type}", tool_version=DECISION_RUNTIME_VERSION, request={"objective": objective}, response=payload, agent_run_id=agent_run_id, snapshot_refs=snapshot_refs)
        return {"tool_result_ids": [snapshot.tool_result_id], "tool_id": f"decision_runtime.{task_type}"}

    @staticmethod
    def _actionable_segment(governed: dict, persisted: dict, mode: Any) -> dict:
        return {"decision_id": persisted.get("decision_id"), "decision_snapshot_id": persisted.get("decision_snapshot_id"), "runtime_mode": mode.value, "proposal": governed["proposal"].to_dict(), "policy": governed["policy_decision"].to_dict(), "final_decision": governed["final_decision"]}


__all__ = ["AnalysisApplicationService"]

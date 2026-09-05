"""Concrete collaborators for the compatibility decision façade.

The façade keeps the historical public API, while these collaborators own
the integration dependencies for each phase.  They intentionally receive a
runtime context instead of reaching into global state, which makes the
freeze/calculation boundary explicit and keeps retries bundle-only.
"""
# The component receives protocol-shaped collaborators at the composition root;
# domain/application packages are checked by the composition gate.
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from app.model_gateway.metrics import TraceContext
from app.skill_loader import load_skills
from contracts.decision import FinalInvestmentDecision, PolicyEvaluation
from contracts.decision_snapshot import (
    DecisionSnapshotV3,
    SnapshotBundleRef,
    canonical_hash,
)
from contracts.evidence import DependencyStatus, DependencyStatusValue, SourceSystem
from contracts.proposal import (
    DecisionHorizon,
    InvestmentProposalV2,
    ModelIdentity,
    NarrativeReport,
)


class BundleFreezeComponent:
    """Collect and persist the immutable input bundle exactly once."""

    def __init__(self, *, evidence_gateway: Any, bundle_builder: Any, bundle_repository: Any, clock: Any = None, trusted_requests: Any = None) -> None:
        self._evidence_gateway = evidence_gateway
        self._bundle_builder = bundle_builder
        self._bundle_repository = bundle_repository
        self._clock = clock
        self._trusted_requests = trusted_requests

    def freeze(self, **kwargs: Any) -> Any:
        task_type = kwargs["task_type"]
        objective = kwargs["objective"]
        subjects = list(kwargs.get("subjects") or [])
        context = {**dict(kwargs.get("context") or {}), "subjects": subjects}
        as_of = kwargs.get("as_of")
        decision_time = self._decision_time(as_of)
        decision_id = kwargs.get("decision_id") or str(uuid4())
        snapshot_id = kwargs.get("snapshot_id") or str(uuid4())
        trace = TraceContext(decision_id=decision_id, snapshot_id=snapshot_id)
        context["trace_context"] = {"trace_id": trace.trace_id, "decision_id": decision_id, "snapshot_id": snapshot_id}
        requested_skill = str(context.get("skill") or task_type).replace("_", "-")
        active_skill = next((item for item in load_skills() if item.slug == requested_skill), None)
        if active_skill is None:
            raise ValueError(f"SKILL_NOT_FOUND: {requested_skill}")
        context["skill"] = requested_skill
        injected = self._trusted_requests(decision_time=decision_time, subjects=subjects, context=context) if callable(self._trusted_requests) else self._trusted_requests
        requested = [dict(item) for item in (injected or []) if isinstance(item, dict)]
        if not requested:
            requested = FormalDecisionCalculatorComponent.evidence_request_plan(task_type, subjects, context, decision_time)
        try:
            evidence, statuses = self._evidence_gateway.collect(requested, decision_time=decision_time, trace=trace)
        except TypeError as exc:
            if "trace" not in str(exc):
                raise
            evidence, statuses = self._evidence_gateway.collect(requested, decision_time=decision_time)
        required_systems = set()
        for evidence_type in active_skill.required_evidence:
            if str(evidence_type).startswith("FACTOR"):
                required_systems.add(SourceSystem.FACTOR)
            elif str(evidence_type) in {"KNOWLEDGE_CLAIM", "CATALYST", "VALUATION_FACT", "EARNINGS_FACT", "RISK_EVENT"}:
                required_systems.add(SourceSystem.CONTENT)
            else:
                required_systems.add(SourceSystem.QUANT)
        present_systems = {item.system for item in statuses}
        statuses.extend(DependencyStatus(system=system, status=DependencyStatusValue.UNAVAILABLE, checked_at=self._clock_now(), reason_codes=["REQUIRED_EVIDENCE_NOT_COLLECTED"]) for system in required_systems - present_systems)
        bundle = self._bundle_builder.build(created_at=self._clock_now(), decision_time=decision_time, task_type=task_type, objective=objective, subjects=subjects, evidence=evidence, dependency_status=statuses, query_context=self._safe_context(context), strategy_context=dict(context.get("strategy_context") or {}))
        self._bundle_repository.save(bundle)
        return bundle

    def _clock_now(self) -> Any:
        value = self._clock() if callable(self._clock) else self._clock.now() if self._clock is not None and hasattr(self._clock, "now") else datetime.now(UTC)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value

    def _decision_time(self, value: Any) -> Any:
        result = value or self._clock_now()
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return result

    @staticmethod
    def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
        allowed = {"skill", "account_id", "mode", "analysis_type", "subjects", "trace_context"}
        return {key: context[key] for key in allowed if key in context}



class FormalDecisionCalculatorComponent:
    """Execute the formal half of the pipeline from a frozen bundle."""

    def __init__(self, *, claude_agent: Any | None = None, trusted_fallback: Any | None = None, policy_engine: Any | None = None, agent_enabled: Callable[[], bool] | None = None, investment_proposal_type: Any | None = None, policy_context_type: Any | None = None, specialist_role_type: Any | None = None, specialist_status_type: Any | None = None, specialist_artifact_type: Any | None = None, quality_fn: Callable[..., Any] | None = None, synthesis_fn: Callable[..., Any] | None = None, operations: dict[str, Callable[..., Any]] | None = None) -> None:
        self.claude_agent = claude_agent
        self.trusted_fallback = trusted_fallback
        self.policy_engine = policy_engine
        self._agent_enabled = agent_enabled or (lambda: False)
        self._investment_proposal_type = investment_proposal_type
        self._policy_context_type = policy_context_type
        self._specialist_role_type = specialist_role_type
        self._specialist_status_type = specialist_status_type
        self._specialist_artifact_type = specialist_artifact_type
        self._quality_fn = quality_fn
        self._synthesis_fn = synthesis_fn
        self.operations = operations or {}

    def _metric(self, name: str, value: float = 1.0, **labels: Any) -> None:
        callback = self.operations.get("metric")
        if callback is not None:
            callback(name, value, **labels)

    @staticmethod
    def evidence_request_plan(task_type: str, subjects: list[str], context: dict[str, Any], decision_time: Any) -> list[dict[str, Any]]:
        slug = str(context.get("skill") or task_type).replace("_", "-")
        skill = next((item for item in load_skills() if item.slug == slug), None)
        requests: list[dict[str, Any]] = []
        for evidence_type in list(skill.required_evidence) if skill is not None else []:
            if evidence_type == "MARKET_SNAPSHOT":
                snapshot_id = str(context.get("trusted_snapshot_id") or "latest")
                requests.append({"source_system": "quant", "operation": "market_snapshot", "evidence_type": evidence_type, "subject_key": "market", "snapshot_id": snapshot_id, "params": {"snapshot_id": snapshot_id}})
            elif evidence_type == "MARKET_REGIME":
                requests.append({"source_system": "quant", "operation": "market_regime", "evidence_type": evidence_type, "subject_key": "market", "params": {"as_of": decision_time.isoformat()}})
            elif evidence_type == "SECTOR_STRENGTH":
                requests.append({"source_system": "quant", "operation": "sector_strength", "evidence_type": evidence_type, "subject_key": "market", "params": {"as_of": decision_time.isoformat()}})
            elif evidence_type.startswith("TECHNICAL"):
                requests.extend({"source_system": "quant", "operation": "technical_evidence", "evidence_type": evidence_type, "subject_key": subject, "params": {"symbol": subject, "as_of": decision_time.isoformat()}} for subject in subjects)
            elif evidence_type.startswith("FACTOR"):
                factor_ids: list[str] = []
                requests.extend({"source_system": "factor", "operation": "factor_evidence", "evidence_type": evidence_type, "subject_key": str(factor_id), "params": {"factor_id": str(factor_id)}} for factor_id in factor_ids)
            elif evidence_type in {"PORTFOLIO_POSITION", "PORTFOLIO_EXPOSURE"}:
                requests.append({"source_system": "quant", "operation": "portfolio_snapshot", "evidence_type": evidence_type, "subject_key": context.get("account_id", "portfolio"), "params": {"account_id": context.get("account_id")}})
            elif evidence_type == "PORTFOLIO_RISK":
                requests.append({"source_system": "quant", "operation": "portfolio_risk", "evidence_type": evidence_type, "subject_key": context.get("account_id", "portfolio"), "params": {"account_id": context.get("account_id")}})
            elif evidence_type in {"KNOWLEDGE_CLAIM", "CATALYST", "VALUATION_FACT", "EARNINGS_FACT", "RISK_EVENT"}:
                requests.extend({"source_system": "content", "operation": "content_search", "evidence_type": evidence_type, "subject_key": str(subject), "params": {"query": str(subject)}} for subject in subjects)
        return requests

    def governance_seed(self, raw: dict[str, Any], proposal: Any, artifacts: list[Any]) -> dict[str, Any]:
        if self._specialist_role_type is None or self._specialist_status_type is None:
            raise ValueError("SPECIALIST_TYPES_REQUIRED")
        risk = next((item for item in artifacts if item.specialist == self._specialist_role_type.RISK), None)
        risk_conclusion = dict(risk.conclusion if risk else {})
        risk_payload = dict(risk_conclusion.get("risk") or {})
        veto = bool(risk_conclusion.get("veto") or risk_payload.get("veto") or risk_payload.get("risk_veto") or (risk is not None and risk.status == self._specialist_status_type.FAILED))
        return {"proposal": proposal, "resolution": {"vetoed": veto, "veto_reasons": ["RISK_VETO"] if veto else []}, "final_decision": {"action": "VETO" if veto else proposal.action, "approved": not veto, "approved_weight": float(proposal.target_weight or 0.0), "vetoed": veto, "veto_reasons": ["RISK_VETO"] if veto else [], "rejections": [], "adjustments": []}}

    @staticmethod
    def missing_context(bundle: Any, context: dict[str, Any], governed: dict[str, Any], proposal: Any) -> list[str]:
        bad = {item.system.value: item for item in bundle.dependency_status if str(item.status) in {"UNAVAILABLE", "STALE"}}
        required = {str(item) for item in (context.get("required_dependencies") or [])}
        reasons = [f"{system}_dependency_{str(item.status).lower()}" for system, item in bad.items() if system in {"quant", "market"} or system in required]
        target_requested = bool(context.get("target_weight") is not None or context.get("requires_portfolio") or (proposal.action in {"BUY", "SELL", "INCREASE", "REDUCE", "EXIT"} and (proposal.target_weight is not None or proposal.weight_delta is not None)))
        if target_requested and not any(item.evidence_type.value.startswith("PORTFOLIO") for item in bundle.evidence):
            reasons.append("portfolio_evidence_required")
        return sorted(set(reasons))

    @staticmethod
    def report_text(report: Any) -> str:
        if isinstance(report, str) and report.strip():
            return report
        if isinstance(report, dict):
            text = report.get("summary") or report.get("text")
            if isinstance(text, str) and text.strip():
                return text
        return "Formal decision produced from the frozen input bundle."

    def proposal_v2(self, raw: dict[str, Any], governed: dict[str, Any], bundle: Any, decision_id: str) -> Any:
        if self._agent_enabled() and isinstance(raw.get("proposal"), dict):
            try:
                return InvestmentProposalV2.model_validate(raw["proposal"])
            except (TypeError, ValueError) as exc:
                raise ValueError("MODEL_PROPOSAL_INVALID: expected InvestmentProposalV2") from exc
        proposal = governed.get("proposal") if isinstance(governed, dict) else None
        if proposal is None:
            local = dict(raw.get("proposal") or {})
            if self._investment_proposal_type is None:
                raise ValueError("INVESTMENT_PROPOSAL_FACTORY_REQUIRED")
            proposal = self._investment_proposal_type(symbol=str(local.get("symbol") or ""), action=str(local.get("action") or "HOLD").upper(), proposed_weight=float(local.get("proposed_weight") or 0.0), confidence=float(local.get("confidence") or 0.0), thesis_refs=list(local.get("thesis_refs") or []), evidence_count=int(local.get("evidence_count") or 0))
        action = proposal.action if proposal.action in {"BUY", "SELL", "HOLD", "INCREASE", "REDUCE", "EXIT", "WATCH"} else "HOLD"
        target = proposal.proposed_weight if action not in {"HOLD", "WATCH"} else None
        return InvestmentProposalV2.build(proposal_id=f"proposal-{decision_id}", subject_type="SYMBOL" if proposal.symbol else "PORTFOLIO", subject_key=proposal.symbol or None, action=action, target_weight=target, weight_delta=None, confidence=proposal.confidence, horizon=DecisionHorizon(period="decision"), thesis=[], catalysts=[], entry_conditions=[], invalidation_conditions=[], expected_risks=[], evidence_refs=[item.evidence_id for item in bundle.evidence], specialist_artifact_refs=[], unknowns=[], generated_by=ModelIdentity(provider=str(raw.get("provider") or "unavailable"), model=str(raw.get("model") or "unavailable"), model_version=str(raw.get("model_version") or "unavailable")))

    @staticmethod
    def policy_v2(governed: dict[str, Any], decision_id: str) -> Any:
        decision = governed["policy_decision"]
        checks = []
        for item in decision.checks or []:
            value = item.__dict__ if hasattr(item, "__dict__") else dict(item)
            passed = bool(value.get("passed", True))
            checks.append({"rule_id": str(value.get("rule") or "POLICY"), "rule_version": decision.policy_version, "passed": passed, "severity": "INFO" if passed else "REJECT", "input_snapshot": {}, "original_value": None, "adjusted_value": None, "reason_code": str(value.get("rule") or "POLICY"), "reason": str(value.get("reason") or value.get("rule") or "deterministic policy check")})
        if not checks:
            passed = bool(decision.approved)
            checks = [{"rule_id": "POLICY", "rule_version": decision.policy_version, "passed": passed, "severity": "INFO" if passed else "REJECT", "input_snapshot": {}, "original_value": None, "adjusted_value": None, "reason_code": "POLICY", "reason": "deterministic policy evaluation"}]
        return PolicyEvaluation.build(policy_result_id=f"policy-{decision_id}", policy_version=decision.policy_version, checks=checks, approved=bool(decision.approved), original_value=governed["proposal"].to_dict(), adjusted_value=decision.approved_weight)

    def evaluate_policy_v2(self, raw: dict[str, Any], governed: dict[str, Any], proposal: Any, bundle: Any, specialist_artifacts: list[Any], request_context: dict[str, Any]) -> Any:
        context = self.policy_context_from_bundle(raw, proposal, bundle, specialist_artifacts, request_context)
        if self.policy_engine is None:
            return self.policy_v2(governed, str(uuid4()))
        evaluated = self.policy_engine.evaluate(proposal, context)
        if isinstance(evaluated, PolicyEvaluation):
            return evaluated
        return self.policy_v2(governed, str(uuid4()))

    def policy_context_from_bundle(self, raw: dict[str, Any], proposal: Any, bundle: Any, specialist_artifacts: list[Any], request_context: dict[str, Any]) -> Any:
        if self._policy_context_type is None:
            raise ValueError("POLICY_CONTEXT_FACTORY_REQUIRED")
        if self._specialist_role_type is None or self._specialist_status_type is None:
            raise ValueError("SPECIALIST_TYPES_REQUIRED")
        specialist_role_type = self._specialist_role_type
        specialist_status_type = self._specialist_status_type
        if self._synthesis_fn is None:
            raise ValueError("EVIDENCE_SYNTHESIS_REQUIRED")
        synthesis = self._synthesis_fn(bundle.evidence, dependencies=bundle.dependency_status, specialist_artifacts=[item.model_dump(mode="json") for item in specialist_artifacts])
        evidence_payloads = [dict(item.payload) for item in bundle.evidence]
        portfolio = next((item for item in evidence_payloads if any(key in item for key in ("gross_exposure", "net_exposure", "positions", "portfolio_available"))), {})
        risk_artifact = next((item for item in specialist_artifacts if item.specialist == specialist_role_type.RISK), None)
        risk_conclusion = dict(risk_artifact.conclusion if risk_artifact else {})
        risk_payload = dict(risk_conclusion.get("risk") or {})
        dependencies = {item.system.value: item.status.value for item in bundle.dependency_status}
        security_rows = [item for item in bundle.evidence if item.source_system == SourceSystem.QUANT and item.subject_key == proposal.subject_key and isinstance(item.payload, dict)]
        def security_fact(name: str) -> bool | None:
            values = [item.payload[name] for item in security_rows if isinstance(item.payload.get(name), bool)]
            if len(set(values)) > 1:
                raise ValueError(f"SECURITY_FACT_CONFLICT:{name}")
            return values[0] if values else None
        is_st, is_suspended, liquidity = security_fact("is_st"), security_fact("is_suspended"), security_fact("liquidity_ok")
        skill_required: list[str] = []
        skill_slug = str(request_context.get("skill") or raw.get("selected_skill") or proposal.subject_type).strip()
        skill = next((item for item in load_skills() if item.slug == skill_slug), None)
        required_domains = ["market"]
        if skill is not None:
            required_domains.extend("factor" for item in skill.required_evidence if str(item).startswith("FACTOR"))
            required_domains.extend("risk" for item in skill.required_evidence if str(item) in {"PORTFOLIO_RISK", "RISK_EVENT"})
            skill_required.extend("factor" for item in skill.required_evidence if str(item).startswith("FACTOR"))
            skill_required.extend("content" for item in skill.required_evidence if str(item) in {"KNOWLEDGE_CLAIM", "CATALYST", "VALUATION_FACT", "EARNINGS_FACT"})
        referenced = [item for item in bundle.evidence if item.evidence_id in set(proposal.evidence_refs)]
        portfolio_items = [item for item in bundle.evidence if item.evidence_type.value.startswith("PORTFOLIO")]
        liquidity_map = {proposal.subject_key: liquidity} if proposal.subject_key and liquidity is not None else {}
        return self._policy_context_type(portfolio_drawdown_mode=bool(portfolio.get("drawdown_mode", False)), restricted_universe=list(portfolio.get("restricted_universe") or []), existing_weights={str(k): float(v) for k, v in (portfolio.get("existing_weights") or {}).items()}, gross_exposure=portfolio.get("gross_exposure"), net_exposure=portfolio.get("net_exposure"), portfolio_available=bool(portfolio) if proposal.target_weight is not None or proposal.weight_delta is not None else True, portfolio_snapshot_at=next((item.as_of for item in portfolio_items), None), portfolio_snapshot_fresh=bool(portfolio and all(item.quality_status.value not in {"STALE", "REJECTED"} for item in portfolio_items)), portfolio_snapshot_freshness_seconds=max([max(0.0, (bundle.decision_time - item.available_at).total_seconds()) for item in portfolio_items] or [0.0]), dependency_health=dependencies, dependency_status=dependencies, evidence_fresh=True if not proposal.evidence_refs else bool(referenced and all(item.quality_status.value not in {"STALE", "REJECTED"} and item.available_at <= bundle.decision_time for item in referenced)), evidence_freshness_seconds=max([max(0.0, (bundle.decision_time - item.available_at).total_seconds()) for item in referenced] or [0.0]), risk_veto=bool(risk_conclusion.get("veto") or risk_payload.get("veto") or risk_payload.get("risk_veto") or (risk_artifact is not None and risk_artifact.status == specialist_status_type.FAILED)), risk_veto_reason=str(risk_payload.get("reason") or ""), domain_coverage=synthesis.coverage.model_dump(mode="json"), required_domains=required_domains, required_dependencies=list(skill_required), skill_required_dependencies=skill_required, dependency_required={}, security_facts_verified={proposal.subject_key: bool(security_rows and proposal.subject_key in liquidity_map and liquidity_map[proposal.subject_key])} if proposal.subject_key else {}, security_is_st={proposal.subject_key: is_st} if proposal.subject_key and is_st is not None else {}, security_is_suspended={proposal.subject_key: is_suspended} if proposal.subject_key and is_suspended is not None else {}, liquidity_ok=liquidity_map)

    @staticmethod
    def final_v2(governed: dict[str, Any], proposal: Any, policy: Any, bundle: Any, decision_id: str, snapshot_id: str, quality: dict[str, Any]) -> Any:
        final = governed["final_decision"]
        veto = final["action"] in {"VETO", "REJECT"} or not final.get("approved", False) or not policy.approved
        approved_weight = policy.adjusted_value if isinstance(policy.adjusted_value, (int, float)) else final.get("approved_weight", 0.0)
        action = proposal.action if proposal.action in {"BUY", "SELL", "HOLD", "INCREASE", "REDUCE", "EXIT", "WATCH"} else "HOLD"
        return FinalInvestmentDecision.build(decision_id=decision_id, decision_time=bundle.decision_time, valid_from=bundle.decision_time, subject_type=proposal.subject_type, subject_key=proposal.subject_key, decision_action="VETO" if veto else ("APPROVE_WITH_ADJUSTMENT" if approved_weight != (proposal.target_weight or 0.0) else "APPROVE"), investment_action=action, target_weight=None if veto or action in {"HOLD", "WATCH"} else approved_weight, weight_delta=None, confidence=proposal.confidence, decision_quality=quality["level"], rationale=list(final.get("veto_reasons") or []) or ["deterministic policy result"], evidence_refs=list(proposal.evidence_refs), proposal_id=proposal.proposal_id, policy_result_id=policy.policy_result_id, invalidation_conditions=list(proposal.invalidation_conditions), bundle_id=bundle.bundle_id, snapshot_id=snapshot_id)

    def formal_quality(self, bundle: Any, governed: dict[str, Any], missing: list[str], context: dict[str, Any], specialist_artifacts: list[Any], proposal: Any) -> dict[str, Any]:
        statuses = [{"system": item.system.value, "status": item.status.value} for item in bundle.dependency_status]
        if self._specialist_artifact_type is None:
            raise ValueError("SPECIALIST_ARTIFACT_TYPE_REQUIRED")
        artifacts = [item.model_dump(mode="json") if isinstance(item, self._specialist_artifact_type) else item for item in specialist_artifacts]
        if self._synthesis_fn is None or self._quality_fn is None:
            raise ValueError("QUALITY_COMPONENTS_REQUIRED")
        synthesis = self._synthesis_fn(bundle.evidence, dependencies=bundle.dependency_status, specialist_artifacts=artifacts)
        assessment = self._quality_fn(coverage=synthesis.coverage.model_dump(mode="json"), dependency_status=statuses, specialist_artifacts=artifacts, target_weight_requested=bool(proposal.target_weight is not None or proposal.weight_delta is not None), evidence_quality=[item.quality_status.value for item in bundle.evidence])
        payload = assessment.model_dump(mode="json")
        payload["degraded_reasons"] = sorted(set(payload.get("degraded_reasons") or []) | set(missing))
        payload["unknowns"] = sorted(set(payload.get("unknowns") or []) | set(missing))
        return payload

    def execute(
        self,
        *,
        task_type: str,
        objective: str,
        context: dict[str, Any],
        decision_time: Any,
        bundle: Any,
        specialist_artifacts: list[Any],
    ) -> dict[str, Any]:
        if self.claude_agent is not None and bool(self.claude_agent.configured()):
            formal_context = {
                "task_type": task_type,
                "objective": objective,
                "subjects": list(context.get("subjects") or []),
                "decision_time": decision_time.isoformat(),
                "decision_input_bundle": bundle.model_dump(mode="json"),
                "specialist_artifacts": [item.model_dump(mode="json") for item in specialist_artifacts],
                "bundle_only": True,
                "allow_external_evidence_calls": False,
                "trace_context": dict(context.get("trace_context") or {}),
            }
            run_formal = getattr(self.claude_agent, "run_formal", None)
            if run_formal is None:
                raise ValueError("MODEL_BUNDLE_ADAPTER_REQUIRED: normal ClaudeAgent.run is not bundle-safe")
            result = run_formal(user_query=objective, context=formal_context, force_skill=context.get("skill"))
            if isinstance(result, dict):
                payload = dict(result)
            else:
                payload = {key: getattr(result, key) for key in ("proposal", "report", "selected_skill", "model", "model_version", "prompt_version", "tool_calls", "missing_evidence_requests", "missing_evidence_reason") if getattr(result, key, None) is not None}
            if not isinstance(payload.get("proposal"), dict):
                raise ValueError("MODEL_PROPOSAL_REQUIRED: formal model output must include canonical proposal")
            if int(payload.get("tool_calls") or 0) > 0:
                raise ValueError("BUNDLE_ONLY_VIOLATION: formal model attempted an external evidence call")
            payload["specialist_artifacts"] = specialist_artifacts
            return payload
        fallback = self.trusted_fallback
        if callable(fallback):
            fallback = fallback(task_type=task_type, objective=objective, context={"subjects": list(context.get("subjects") or [])}, bundle=bundle, specialist_artifacts=specialist_artifacts)
        if isinstance(fallback, dict):
            return {**fallback, "runtime_mode": "DETERMINISTIC_FALLBACK", "specialist_artifacts": specialist_artifacts}
        return {"report": "Model unavailable; decision is degraded and contains no fabricated external facts.", "runtime_mode": "DETERMINISTIC_FALLBACK", "specialist_artifacts": specialist_artifacts}

    def calculate(self, *, task_type: str, objective: str, subjects: list[str], context: dict[str, Any] | None, as_of: Any, decision_id: str | None, snapshot_id: str | None, frozen_bundle: Any) -> dict[str, Any]:
        """Run the formal orchestration using injected phase ports only."""
        if frozen_bundle is None:
            raise ValueError("FORMAL_CALCULATION_REQUIRES_FROZEN_BUNDLE")
        context = {**dict(context or {}), "subjects": list(subjects)}
        frozen_trace = (frozen_bundle.query_context or {}).get("trace_context") if isinstance(frozen_bundle.query_context, dict) else None
        decision_id = decision_id or str((frozen_trace or {}).get("decision_id") or "") or str(uuid4())
        snapshot_id = snapshot_id or str((frozen_trace or {}).get("snapshot_id") or "") or str(uuid4())
        decision_time = frozen_bundle.decision_time
        trace = TraceContext(trace_id=str((frozen_trace or {}).get("trace_id") or ""), decision_id=decision_id, bundle_id=frozen_bundle.bundle_id, snapshot_id=snapshot_id)
        context["trace_context"] = {"trace_id": trace.trace_id, "decision_id": decision_id, "bundle_id": frozen_bundle.bundle_id, "snapshot_id": snapshot_id}
        requested_skill = str(context.get("skill") or task_type).replace("_", "-")
        active_skill = next((item for item in load_skills() if item.slug == requested_skill), None)
        if active_skill is None:
            raise ValueError(f"SKILL_NOT_FOUND: {requested_skill}")
        context["skill"] = requested_skill
        started = monotonic()
        statuses = list(frozen_bundle.dependency_status)
        self._metric("bundle_build_latency_seconds", monotonic() - started, task_type=task_type)
        specialist_started = monotonic()
        specialists = self.operations["specialists"](task_type=task_type, objective=objective, context=context, decision_time=decision_time, bundle=frozen_bundle)
        self._metric("specialist_latency_seconds", monotonic() - specialist_started, task_type=task_type)
        raw = self.execute(task_type=task_type, objective=objective, context=context, decision_time=decision_time, bundle=frozen_bundle, specialist_artifacts=specialists)
        proposal = self.proposal_v2(raw, {}, frozen_bundle, decision_id)
        refs = [item.artifact_id for item in specialists]
        if proposal.specialist_artifact_refs and not set(proposal.specialist_artifact_refs).issubset(set(refs)):
            raise ValueError("MODEL_PROPOSAL_INVALID: specialist_artifact_refs must reference actual artifacts")
        if not proposal.specialist_artifact_refs:
            values = proposal.model_dump(mode="python", exclude={"proposal_hash"})
            values["specialist_artifact_refs"] = refs
            proposal = InvestmentProposalV2.build(**values)
        governed = self.governance_seed(raw, proposal, specialists)
        missing = self.missing_context(frozen_bundle, context, governed, proposal)
        if missing:
            governed["final_decision"] = {**governed["final_decision"], "action": "VETO", "approved": False, "approved_weight": 0.0, "vetoed": True, "veto_reasons": list(governed["final_decision"].get("veto_reasons") or []) + missing}
        quality = self.formal_quality(frozen_bundle, governed, missing, context, specialists, proposal)
        policy = self.evaluate_policy_v2(raw, governed, proposal, frozen_bundle, specialists, context)
        final = self.final_v2(governed, proposal, policy, frozen_bundle, decision_id, snapshot_id, quality)
        self._metric("decision_quality_total", level=quality["level"], task_type=task_type)
        if final.decision_action == "VETO":
            self._metric("risk_veto_total", task_type=task_type)
        report = NarrativeReport(summary=self.report_text(raw.get("report")))
        snapshot = self.operations["snapshot"](snapshot_id=snapshot_id, decision_id=decision_id, decision_time=decision_time, task_type=task_type, context=context, bundle=frozen_bundle, statuses=statuses, raw=raw, proposal=proposal, policy=policy, final=final, quality=quality, patches=[])
        saved = self.operations["save_decision"](id=decision_id, query=objective, candidates=[{"symbol": subject} for subject in subjects], decision_quality=quality["level"], decision_snapshot={}, persist_snapshot=False, schedule_evaluations=False)
        actual_id = saved.get("decision_id", decision_id) if isinstance(saved, dict) else decision_id
        if actual_id != decision_id:
            raise ValueError("DecisionService returned an id different from the v3 lineage")
        persist_segments = getattr(self.operations["snapshot_repository"], "save_formal_segments", None)
        if persist_segments is not None:
            persist_segments(decision_id=decision_id, bundle_id=frozen_bundle.bundle_id, artifacts=specialists, proposal=proposal, policy=policy)
        snapshot_repository: Any = self.operations["snapshot_repository"]
        snapshot_repository.save_v3(snapshot)
        self._metric("decision_latency_seconds", monotonic() - started, task_type=task_type)
        return {"decision": final.model_dump(mode="json"), "snapshot_id": snapshot.snapshot_id, "decision_quality": quality, "report": report.model_dump(mode="json"), "decision_id": actual_id, "bundle_id": frozen_bundle.bundle_id}


class SnapshotBuilderComponent:
    """Build the v3 snapshot through the runtime's injected repositories."""

    def __init__(self, build_fn: Callable[..., Any] | None = None, *, agent_enabled: Callable[[], bool] | None = None, policy_engine: Any | None = None, skill_snapshot: Callable[..., dict] | None = None, specialist_artifact_type: Any | None = None) -> None:
        self._build_fn = build_fn
        self._agent_enabled = agent_enabled or (lambda: False)
        self._policy_engine = policy_engine
        self._skill_snapshot = skill_snapshot
        self._specialist_artifact_type = specialist_artifact_type

    @staticmethod
    def skill_snapshot(raw: dict[str, Any], context: dict[str, Any], task_type: str) -> dict[str, Any]:
        slug = str(raw.get("selected_skill") or context.get("skill") or task_type)
        skill = next((item for item in load_skills() if item.slug == slug), None)
        if skill is None:
            raise ValueError(f"SKILL_NOT_FOUND: {slug}")
        return {"slug": skill.slug, "version": str(skill.version), "contract_hash": str(skill.skill_contract_hash), "markdown_hash": str(skill.skill_markdown_hash), "required_specialists": list(skill.required_specialists), "required_evidence": list(skill.required_evidence)}

    def build(self, **kwargs: Any) -> Any:
        if self._build_fn is not None:
            return self._build_fn(**kwargs)
        snapshot_id = kwargs["snapshot_id"]
        decision_id = kwargs["decision_id"]
        decision_time = kwargs["decision_time"]
        task_type = kwargs["task_type"]
        context = kwargs["context"]
        bundle = kwargs["bundle"]
        raw = kwargs["raw"]
        proposal = kwargs["proposal"]
        policy = kwargs["policy"]
        final = kwargs["final"]
        quality = kwargs["quality"]
        patches = kwargs.get("patches") or []
        evidence_payloads = {item.evidence_id: item.model_dump(mode="json") for item in bundle.evidence}
        evidence_hashes = {key: canonical_hash(value) for key, value in evidence_payloads.items()}
        content_ids = sorted({str(item.snapshot_id or item.payload.get("content_snapshot_id")) for item in bundle.evidence if item.source_system == SourceSystem.CONTENT and (item.snapshot_id or item.payload.get("content_snapshot_id"))})
        market_ids = sorted({str(item.snapshot_id or item.payload.get("snapshot_id")) for item in bundle.evidence if item.source_system == SourceSystem.QUANT and item.evidence_type.value == "MARKET_SNAPSHOT" and (item.snapshot_id or item.payload.get("snapshot_id"))})
        factor_nodes = [{"type": "FACTOR_ARTIFACT", "id": str(item.payload["factor_artifact_id"]), "hash": canonical_hash({"source_ref": item.source_ref, "snapshot_id": item.snapshot_id or item.payload.get("snapshot_id"), "evidence_id": item.evidence_id})} for item in bundle.evidence if item.source_system == SourceSystem.FACTOR and item.payload.get("factor_artifact_id") and (item.snapshot_id or item.payload.get("snapshot_id")) and item.source_ref]
        artifacts = list(raw.get("specialist_artifacts") or [])
        if self._specialist_artifact_type is None:
            raise ValueError("SPECIALIST_ARTIFACT_TYPE_REQUIRED")
        parsed = [item if isinstance(item, self._specialist_artifact_type) else self._specialist_artifact_type.model_validate(item) for item in artifacts]
        artifact_refs = [item.artifact_id for item in parsed]
        artifact_hashes = {item.artifact_id: item.artifact_hash for item in parsed}
        artifact_payloads = {item.artifact_id: item.model_dump(mode="json", exclude={"artifact_hash"}) for item in parsed}
        dependency_states = {item.system.value: {"status": item.status.value, "checked_at": item.checked_at.isoformat(), "contract_version": item.contract_version, "service_version": item.service_version, "snapshot_id": item.snapshot_id, "reason_codes": list(item.reason_codes)} for item in bundle.dependency_status}
        for system in ("quant", "factor", "content"):
            dependency_states.setdefault(system, {"status": "UNAVAILABLE", "reason_codes": ["NOT_COLLECTED"]})
        model_meta = raw.get("model_identity") if isinstance(raw.get("model_identity"), dict) else {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        input_tokens = int(usage.get("input_tokens", raw.get("input_tokens", 0)) or 0)
        output_tokens = int(usage.get("output_tokens", raw.get("output_tokens", 0)) or 0)
        total_tokens = int(usage.get("total_tokens", raw.get("total_tokens", raw.get("tokens", 0))) or 0) or input_tokens + output_tokens
        actual_cost = usage.get("cost", raw.get("cost", usage.get("estimated_cost", raw.get("estimated_cost", 0.0))))
        return DecisionSnapshotV3(
            snapshot_id=snapshot_id, decision_id=decision_id, decision_time=decision_time,
            runtime={"runtime_mode": raw.get("runtime_mode") or ("PRIMARY_AGENT" if self._agent_enabled() else "DETERMINISTIC_FALLBACK"), "workflow_version": "decision-runtime.v3", "fallback_used": not self._agent_enabled(), "trace_id": str(context.get("trace_context", {}).get("trace_id")), "decision_id": decision_id, "bundle_id": bundle.bundle_id, "snapshot_id": snapshot_id},
            input_bundle=SnapshotBundleRef(bundle_id=bundle.bundle_id, bundle_hash=bundle.bundle_hash, schema_version=bundle.schema_version, payload=bundle.model_dump(mode="json")),
            dependencies={"states": dependency_states, **dependency_states, "bundle_id": bundle.bundle_id, "bundle_hash": bundle.bundle_hash, "patches": [{"patch_id": item.patch_id, "previous_hash": item.previous_hash, "new_hash": item.new_hash} for item in patches]},
            evidence={"refs": [{"id": key} for key in evidence_payloads], "hashes": evidence_hashes, "payloads": evidence_payloads},
            specialists={"artifact_refs": artifact_refs, "artifact_hashes": artifact_hashes, "payloads": artifact_payloads},
            model={"provider": str(raw.get("provider") or model_meta.get("provider") or proposal.generated_by.provider), "model": str(raw.get("model") or model_meta.get("model") or proposal.generated_by.model), "model_version": str(raw.get("model_version") or model_meta.get("model_version") or proposal.generated_by.model_version or "unavailable"), "prompt_version": str(raw.get("prompt_version") or model_meta.get("prompt_version") or "unavailable")},
            skill=self._skill_snapshot(raw, context, task_type) if self._skill_snapshot else self.skill_snapshot(raw, context, task_type),
            proposal={"proposal_id": proposal.proposal_id, "proposal_hash": proposal.proposal_hash, "payload": proposal.model_dump(mode="json", exclude={"proposal_hash"})},
            conflicts=list(raw.get("conflicts") or []), risk={"veto": final.decision_action == "VETO", "reasons": list(final.rationale), "risk_rule_version": str(getattr(self._policy_engine, "risk_rule_version", None) or "policy-risk.v2")},
            policy={"policy_result_id": policy.policy_result_id, "policy_version": policy.policy_version, "checks": policy.model_dump(mode="json")["checks"], "approved": policy.approved, "original_value": policy.original_value, "adjusted_value": policy.adjusted_value, "result_hash": policy.result_hash},
            output={"decision_id": decision_id, "final_decision_id": final.decision_id, "bundle_id": bundle.bundle_id, "proposal_id": proposal.proposal_id, "policy_result_id": policy.policy_result_id, "final_decision": final.model_dump(mode="json", exclude={"decision_hash"}), "final_decision_hash": canonical_hash(final.model_dump(mode="json", exclude={"decision_hash"}))},
            lineage=[{"type": "BUNDLE", "id": bundle.bundle_id, "hash": bundle.bundle_hash}, *[{"type": "BUNDLE_PATCH", "id": item.patch_id, "hash": item.new_hash} for item in patches], *[{"type": "EVIDENCE", "id": key, "hash": value} for key, value in evidence_hashes.items()], *[{"type": "CONTENT_SNAPSHOT", "id": value} for value in content_ids], *[{"type": "MARKET_SNAPSHOT", "id": value} for value in market_ids], *factor_nodes, *[{"type": "SPECIALIST_ARTIFACT", "id": key, "hash": value} for key, value in artifact_hashes.items()], {"type": "PROPOSAL", "id": proposal.proposal_id, "hash": proposal.proposal_hash}, {"type": "POLICY", "id": policy.policy_result_id, "hash": policy.result_hash}, {"type": "FINAL_DECISION", "id": final.decision_id, "hash": final.decision_hash}, {"type": "DECISION", "id": decision_id, "hash": final.decision_hash}, {"type": "TRACE", "id": str(context.get("trace_context", {}).get("trace_id")), "hash": canonical_hash(context.get("trace_context", {}))}],
            decision_quality=quality,
            usage={"tool_calls": int(usage.get("tool_calls", raw.get("tool_calls") or sum(item.tool_usage.calls for item in parsed))), "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens, "tokens": total_tokens, "latency_ms": int(usage.get("latency_ms", raw.get("latency_ms", 0)) or 0), "specialist_tool_calls": sum(item.tool_usage.calls for item in parsed), "cost": actual_cost, "estimated_cost": usage.get("estimated_cost", actual_cost)},
        )

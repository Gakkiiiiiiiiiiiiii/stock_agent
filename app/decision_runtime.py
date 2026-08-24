"""唯一 DecisionRuntime（P0 A-01）。

强制主流程（不允许第二条决策链路）：

    Request
      → RuntimeMode resolution（PRIMARY_AGENT / DETERMINISTIC_FALLBACK /
        DEGRADED_AGENT / REPLAY，显式枚举）
      → Supervisor / TaskGraph（specialist artifacts）
      → Conflict Resolver（Risk 拥有 VETO 权）
      → InvestmentProposal（LLM/执行适配器只产出提案）
      → PolicyEngine（reject / resize / downgrade，最终决策权威）
      → Suitability（需要时；FAIL 时不得输出不符合策略的 actionable advice）
      → Final Decision
      → DecisionService.save_decision()
      → DecisionSnapshot v2
      → Response

ClaudeAgent / LocalFallbackOrchestrator 只是本 Runtime 的内部 execution
adapter：LLM execution authority ≠ Decision authority。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from time import monotonic
from uuid import uuid4
from typing import Any

from agent.contracts import AgentArtifact, AgentRole, AgentTask, TaskStatus, SpecialistArtifact, SpecialistRole
from agent.decision_quality import compute_decision_quality_v2
from agent.plans.daily_market_decision import build_daily_market_decision_graph
from agent.specialists import FactorSpecialist, MarketSpecialist, PortfolioSpecialist, ResearchSpecialist, RiskSpecialist, TechnicalSpecialist
from agent.specialists.base import ToolSpecialist
from agent.shared_context import SharedContext
from agent.contracts import SpecialistStatus, ToolUsage, SpecialistArtifact
from agent.task_graph import TaskGraph
from agent.supervisor import Supervisor
from agent.task_graph import TaskGraph
from app.claude_agent import ClaudeAgent
from app.fallback_orchestrator import LocalFallbackOrchestrator
from app.skill_loader import load_skills
from contracts.decision_input import DecisionInputBundle, DecisionInputBundlePatch
from contracts.evidence import DependencyStatus, DependencyStatusValue, SourceSystem
from contracts.decision_snapshot import DecisionSnapshotV3, SnapshotBundleRef, canonical_hash
from contracts.proposal import DecisionHorizon, InvestmentProposalV2, ModelIdentity, NarrativeReport
from contracts.decision import FinalInvestmentDecision, PolicyEvaluation
from contracts.content import CONTENT_FACTOR_SIGNAL_VERSION
from engines.advisory.models import InvestorProfile, Recommendation
from engines.advisory.suitability import evaluate_suitability
from engines.decision.conflict_resolver import resolve_conflicts_v2
from engines.decision.decision_service import DecisionService
from engines.decision.runtime_mode import RuntimeMode, build_runtime_segment
from engines.policy.engine import PolicyEngine
from engines.policy.models import InvestmentProposal, PolicyContext
from storage.repositories.tool_result_repository import ToolResultRepository
from services.evidence.gateway import EvidenceGateway
from services.evidence.bundle import DecisionInputBundleBuilder, DecisionInputBundleResolver
from agent.evidence_synthesis import synthesize_evidence
from storage.repositories.decision_input_repository import DecisionInputBundleRepository
from storage.repositories.research_repository import DecisionSnapshotRepository
from app.model_gateway.metrics import MetricsRecorder, TraceContext, global_metrics

DECISION_RUNTIME_VERSION = "decision-runtime.v1"
FORMAL_WORKFLOW_VERSION = "decision-runtime.v3"
# v2 仅允许显式 legacy mode（只服务旧 Release lane），main 默认 v3。
LEGACY_SIGNAL_CONTRACT_VERSION = "content-factor-signal.v2"


class _BundleToolRegistry:
    """Read-only specialist adapter backed exclusively by frozen evidence."""

    def __init__(self, bundle: DecisionInputBundle) -> None:
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


class _BundleFactorClient:
    def __init__(self, bundle: DecisionInputBundle) -> None:
        self.bundle = bundle

    def score_alpha(self, request) -> dict:
        scores = [{**dict(item.payload), "evidence_id": item.evidence_id} for item in self.bundle.evidence if item.evidence_type.value in {"FACTOR_SCORE", "FACTOR_SET", "FACTOR_RESEARCH_RESULT"}]
        return {"scores": scores, "factor_set_version": next((item.data_version for item in self.bundle.evidence if item.evidence_type.value == "FACTOR_SET"), None), "as_of": request.as_of}


class DecisionRuntime:
    """唯一决策主流程。所有 actionable 输出都必须经过 Policy/Risk 治理并落库。"""

    def __init__(
        self,
        *,
        claude_agent: ClaudeAgent | None = None,
        fallback: LocalFallbackOrchestrator | None = None,
        decision_service: DecisionService | None = None,
        policy_engine: PolicyEngine | None = None,
        tool_results: ToolResultRepository | None = None,
        evidence_gateway: EvidenceGateway | None = None,
        bundle_repository: DecisionInputBundleRepository | None = None,
        clock: Any | None = None,
        snapshot_repository: DecisionSnapshotRepository | None = None,
        trusted_evidence_requests: Callable[..., list[dict[str, Any]]] | list[dict[str, Any]] | None = None,
        trusted_fallback: Callable[..., dict[str, Any]] | dict[str, Any] | None = None,
        outcome_provider: Any | None = None,
        review_provider: Any | None = None,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        self.claude_agent = claude_agent if claude_agent is not None else ClaudeAgent()
        self.fallback = fallback if fallback is not None else LocalFallbackOrchestrator()
        self.decision_service = decision_service or DecisionService(clock=clock)
        self.policy_engine = policy_engine or PolicyEngine()
        self.tool_results = tool_results or ToolResultRepository()
        self.evidence_gateway = evidence_gateway or EvidenceGateway()
        self.bundle_repository = bundle_repository or DecisionInputBundleRepository()
        self.bundle_builder = DecisionInputBundleBuilder()
        self.bundle_resolver = DecisionInputBundleResolver()
        self.clock = clock
        self.snapshot_repository = snapshot_repository or DecisionSnapshotRepository()
        self.trusted_evidence_requests = trusted_evidence_requests
        self.trusted_fallback = trusted_fallback
        from engines.decision.outcome_service import OutcomeService
        from engines.decision.review_service import ReviewService
        self.outcome_provider = outcome_provider or OutcomeService(clock=clock)
        self.review_provider = review_provider or ReviewService(clock=clock)
        self.metrics = metrics or global_metrics()

    # ---------------------------------------------------------------- RuntimeMode
    def agent_enabled(self) -> bool:
        return bool(self.claude_agent.configured())

    def resolve_runtime_mode(self) -> tuple[RuntimeMode, str | None]:
        """RuntimeMode 必须显式：不得只靠 'claude-style-agent'/'local-fallback' 字符串表达。"""
        if self.agent_enabled():
            return RuntimeMode.PRIMARY_AGENT, None
        return RuntimeMode.DETERMINISTIC_FALLBACK, "MODEL_UNAVAILABLE"

    # ---------------------------------------------------------------- public entries
    def analyze_stock(self, symbol: str, as_of: date | None = None, patterns: list[str] | None = None) -> dict:
        query = f"分析股票 {symbol} 当前是否存在技术机会，并给出风险和操作条件。"

        def _execute() -> dict:
            if self.agent_enabled():
                result = self.claude_agent.run(
                    user_query=query,
                    context={"symbol": symbol, "date": str(as_of) if as_of else None, "patterns": patterns},
                    force_skill="a-share-technical-analysis",
                )
                return {
                    "symbol": symbol,
                    "date": str(as_of) if as_of else None,
                    "orchestration": "claude-style-agent",
                    "selected_skill": result.selected_skill,
                    "selection_reason": result.selection_reason,
                    "tool_calls": result.tool_calls,
                    "trace": result.trace,
                    "report": result.report,
                }
            return self.fallback.analyze_stock(symbol, as_of=as_of, patterns=patterns)

        return self._run_narrative(
            task_type="analyze_stock",
            role=AgentRole.TECHNICAL,
            objective=query,
            execute=_execute,
            query=query,
            subject=symbol,
            as_of=as_of,
        )

    def analyze_theme(self, theme_name: str) -> dict:
        query = f"分析主题 {theme_name} 的投资逻辑是否成立，并输出催化、标的、触发和证伪条件。"

        def _execute() -> dict:
            if self.agent_enabled():
                result = self.claude_agent.run(
                    user_query=query,
                    context={"theme_name": theme_name},
                    force_skill="industry-logic-research",
                )
                return {
                    "theme_name": theme_name,
                    "orchestration": "claude-style-agent",
                    "selected_skill": result.selected_skill,
                    "selection_reason": result.selection_reason,
                    "tool_calls": result.tool_calls,
                    "trace": result.trace,
                    "report": result.report,
                }
            return self.fallback.analyze_theme(theme_name)

        return self._run_narrative(
            task_type="analyze_theme",
            role=AgentRole.RESEARCH,
            objective=query,
            execute=_execute,
            query=query,
            subject=theme_name,
        )

    def daily_scan(self, scan_date: date | None = None, mode: str = "after_close") -> dict:
        scan_day = scan_date or self._clock_now().date()
        query = f"请完成 {scan_day} {mode} 的每日市场扫描，输出强主题、候选方向、仓位建议和风险提示。"

        def _execute() -> dict:
            if self.agent_enabled():
                result = self.claude_agent.run(
                    user_query=query,
                    context={"date": str(scan_date) if scan_date else None, "mode": mode},
                    force_skill="daily-market-decision",
                )
                return {
                    "date": str(scan_day),
                    "mode": mode,
                    "orchestration": "claude-style-agent",
                    "selected_skill": result.selected_skill,
                    "selection_reason": result.selection_reason,
                    "tool_calls": result.tool_calls,
                    "trace": result.trace,
                    "report": result.report,
                }
            return self.fallback.daily_scan(scan_date=scan_date, mode=mode)

        return self._run_narrative(
            task_type="daily_scan",
            role=AgentRole.MARKET,
            objective=query,
            execute=_execute,
            query=query,
            subject=None,
            as_of=scan_date,
        )

    def run(
        self,
        query: str,
        context: dict | None = None,
        skill: str | None = None,
        emit: Callable[[str, dict], None] | None = None,
    ) -> dict:
        """通用 actionable入口（run_agent）：同样必须走完整治理链路。"""
        # Public v1 agent execution is intentionally narrative-only.  Formal
        # actionable decisions must enter through ``decide``/v2.
        return self._run_narrative(task_type="run_agent", role=AgentRole.RESEARCH, objective=query, execute=lambda: self.claude_agent.run(user_query=query, context=self._safe_formal_context(context or {}), force_skill=skill, emit=emit).__dict__ if self.agent_enabled() else self.fallback.analyze_theme(query), query=query, subject=None, as_of=None, emit=emit)

    # ------------------------------------------------------------ formal v2 entry
    def decide(
        self,
        *,
        task_type: str,
        objective: str,
        subjects: list[str] | None = None,
        context: dict[str, Any] | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Bundle-first formal decision entry point used by ``/api/v2``.

        The bundle is persisted before the model/fallback adapter is invoked.
        Any additional evidence is accepted only through a persisted patch.
        """
        context = {**dict(context or {}), "subjects": list(subjects or [])}
        decision_time = self._decision_time(as_of)
        decision_id = str(uuid4())
        snapshot_id = str(uuid4())
        trace = TraceContext(decision_id=decision_id, snapshot_id=snapshot_id)
        context["trace_context"] = {"trace_id": trace.trace_id, "decision_id": decision_id, "snapshot_id": snapshot_id}
        requested_skill = str(context.get("skill") or task_type)
        if not any(item.slug == requested_skill for item in load_skills()):
            requested_skill = requested_skill.replace("_", "-")
        if not any(item.slug == requested_skill for item in load_skills()):
            raise ValueError(f"SKILL_NOT_FOUND: {requested_skill}")
        context["skill"] = requested_skill
        active_skill = next(item for item in load_skills() if item.slug == requested_skill)
        injected = self.trusted_evidence_requests(decision_time=decision_time, subjects=list(subjects or []), context=context) if callable(self.trusted_evidence_requests) else self.trusted_evidence_requests
        requested = [dict(item) for item in (injected or []) if isinstance(item, dict)]
        if not requested:
            requested = self._evidence_request_plan(task_type, list(subjects or []), context, decision_time)
        bundle_started = monotonic()
        collect = self.evidence_gateway.collect
        try:
            evidence, statuses = collect(requested, decision_time=decision_time, trace=trace)
        except TypeError as exc:
            # Small offline test doubles predating the trace-aware gateway are
            # still valid adapters; production gateways receive the same trace.
            if "trace" not in str(exc):
                raise
            evidence, statuses = collect(requested, decision_time=decision_time)
        required_systems = set()
        for evidence_type in active_skill.required_evidence:
            if str(evidence_type).startswith("FACTOR"):
                required_systems.add(SourceSystem.FACTOR)
            elif str(evidence_type) in {"KNOWLEDGE_CLAIM", "CATALYST", "VALUATION_FACT", "EARNINGS_FACT", "RISK_EVENT"}:
                required_systems.add(SourceSystem.CONTENT)
            else:
                required_systems.add(SourceSystem.QUANT)
        present_systems = {item.system for item in statuses}
        statuses.extend(
            DependencyStatus(system=system, status=DependencyStatusValue.UNAVAILABLE, checked_at=self._clock_now(), reason_codes=["REQUIRED_EVIDENCE_NOT_COLLECTED"])
            for system in required_systems - present_systems
        )
        bundle = self.bundle_builder.build(
            created_at=self._clock_now(), decision_time=decision_time, task_type=task_type,
            objective=objective, subjects=list(subjects or []), evidence=evidence,
            dependency_status=statuses, query_context=self._safe_formal_context(context), strategy_context=dict(context.get("strategy_context") or {}),
        )
        self.bundle_repository.save(bundle)
        trace = TraceContext(trace_id=trace.trace_id, decision_id=decision_id, bundle_id=bundle.bundle_id, snapshot_id=snapshot_id)
        context["trace_context"] = {"trace_id": trace.trace_id, "decision_id": decision_id, "bundle_id": bundle.bundle_id, "snapshot_id": snapshot_id}
        self.metrics.observe("bundle_build_latency_seconds", monotonic() - bundle_started, task_type=task_type)

        specialists_started = monotonic()
        specialist_artifacts = self._run_bundle_specialists(task_type, objective, context, decision_time, bundle)
        self.metrics.observe("specialist_latency_seconds", monotonic() - specialists_started, task_type=task_type)
        patches = []
        specialist_patch_requests = [request for item in specialist_artifacts for request in (item.conclusion.get("missing_evidence_requests") or []) if isinstance(request, dict)]
        if specialist_patch_requests:
            try:
                patch_evidence, patch_statuses = collect(specialist_patch_requests, decision_time=decision_time, trace=trace)
            except TypeError as exc:
                if "trace" not in str(exc):
                    raise
                patch_evidence, patch_statuses = collect(specialist_patch_requests, decision_time=decision_time)
            statuses.extend(patch_statuses)
            previous_bundle = bundle
            bundle, patch = self.bundle_resolver.patch(bundle, reason="specialist requested missing evidence", evidence=patch_evidence, created_at=self._clock_now())
            final_values = bundle.model_dump(mode="python")
            final_values["dependency_status"] = list(statuses)
            final_values["bundle_hash"] = ""
            bundle = DecisionInputBundle.model_validate(final_values)
            patch = DecisionInputBundlePatch(patch_id=patch.patch_id, reason=patch.reason, created_at=patch.created_at, evidence=patch.evidence, previous_hash=previous_bundle.bundle_hash, new_hash=bundle.bundle_hash)
            patches.append(patch)
            apply_patch = getattr(self.bundle_repository, "apply_patch", None)
            if apply_patch is not None:
                apply_patch(bundle=bundle, patch=patch)
            else:
                self.bundle_repository.save_patch(bundle=previous_bundle, patch=patch)
                save_patched = getattr(self.bundle_repository, "save_patched", None)
                (save_patched or self.bundle_repository.save)(bundle, patch) if save_patched else self.bundle_repository.save(bundle)
            specialist_artifacts = self._run_bundle_specialists(task_type, objective, context, decision_time, bundle)
        raw = self._formal_execute(task_type, objective, context, decision_time, bundle, specialist_artifacts)
        report_text = self._report_text(raw.get("report"))
        proposal = self._proposal_v2(raw, {}, bundle, decision_id)
        specialist_refs = [item.artifact_id for item in specialist_artifacts]
        if proposal.specialist_artifact_refs and not set(proposal.specialist_artifact_refs).issubset(set(specialist_refs)):
            raise ValueError("MODEL_PROPOSAL_INVALID: specialist_artifact_refs must reference actual artifacts")
        if not proposal.specialist_artifact_refs:
            proposal_values = proposal.model_dump(mode="python", exclude={"proposal_hash"})
            proposal_values["specialist_artifact_refs"] = specialist_refs
            proposal = InvestmentProposalV2.build(**proposal_values)
        governed = self._formal_governance_seed(raw, proposal, specialist_artifacts)
        missing = self._missing_context(bundle, context, governed, proposal)
        if missing:
            governed["final_decision"] = {
                **governed["final_decision"], "action": "VETO", "approved": False,
                "approved_weight": 0.0, "vetoed": True,
                "veto_reasons": list(governed["final_decision"].get("veto_reasons") or []) + missing,
            }
        quality = self._formal_quality(bundle, governed, missing, context, specialist_artifacts, proposal)
        policy = self._evaluate_policy_v2(raw, governed, proposal, bundle, specialist_artifacts, context)
        final = self._final_v2(governed, proposal, policy, bundle, decision_id, snapshot_id, quality)
        self.metrics.increment("decision_quality_total", level=quality["level"], task_type=task_type)
        if final.decision_action == "VETO":
            self.metrics.increment("risk_veto_total", task_type=task_type)
        policy_adjustments = sum(1 for check in policy.checks if getattr(check, "severity", None) == "ADJUST")
        if policy_adjustments:
            self.metrics.increment("policy_adjustment_total", amount=policy_adjustments, task_type=task_type)
        degraded = sum(1 for item in statuses if getattr(item.status, "value", item.status) in {"DEGRADED", "UNAVAILABLE", "ERROR"})
        if degraded:
            self.metrics.increment("dependency_degraded_total", amount=degraded, task_type=task_type)
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        tool_calls = usage.get("tool_calls") or usage.get("specialist_tool_calls") or 0
        if isinstance(tool_calls, (int, float)) and tool_calls:
            self.metrics.increment("tool_calls_total", amount=tool_calls, task_type=task_type)
        report = NarrativeReport(summary=report_text)
        snapshot = self._snapshot_v3(
            snapshot_id=snapshot_id, decision_id=decision_id, decision_time=decision_time,
            task_type=task_type, context=context, bundle=bundle, statuses=statuses,
            raw=raw, proposal=proposal, policy=policy, final=final, quality=quality,
            patches=patches,
        )
        saved = self.decision_service.save_decision(
            id=decision_id, query=objective,
            candidates=[{"symbol": subject} for subject in subjects or []],
            decision_quality=quality["level"], decision_snapshot={}, persist_snapshot=False, schedule_evaluations=False,
        )
        actual_decision_id = saved.get("decision_id", decision_id) if isinstance(saved, dict) else decision_id
        if actual_decision_id != decision_id:
            raise ValueError("DecisionService returned an id different from the v3 lineage")
        persist_segments = getattr(self.snapshot_repository, "save_formal_segments", None)
        if persist_segments is not None:
            persist_segments(decision_id=decision_id, bundle_id=bundle.bundle_id, artifacts=specialist_artifacts, proposal=proposal, policy=policy)
        self.snapshot_repository.save_v3(snapshot)
        self.metrics.observe("decision_latency_seconds", monotonic() - bundle_started, task_type=task_type)
        return {
            "decision": final.model_dump(mode="json"),
            "snapshot_id": snapshot.snapshot_id,
            "decision_quality": quality,
            "report": report.model_dump(mode="json"),
            "decision_id": actual_decision_id,
            "bundle_id": bundle.bundle_id,
        }

    formal_decision = decide

    @staticmethod
    def _safe_formal_context(context: dict[str, Any]) -> dict[str, Any]:
        """Persist only business selectors; public callers cannot persist facts."""
        allowed = {"skill", "account_id", "mode", "analysis_type", "subjects"}
        return {key: context[key] for key in allowed if key in context}

    @staticmethod
    def _evidence_request_plan(task_type: str, subjects: list[str], context: dict[str, Any], decision_time: datetime) -> list[dict[str, Any]]:
        slug = str(context.get("skill") or task_type).replace("_", "-")
        skill = next((item for item in load_skills() if item.slug == slug), None)
        required = list(skill.required_evidence) if skill is not None else []
        requests: list[dict[str, Any]] = []
        for evidence_type in required:
            if evidence_type == "MARKET_SNAPSHOT":
                # Initial collection is allowed to resolve the provider's
                # current snapshot.  The returned concrete snapshot/hash is
                # frozen into the bundle; replay never calls this planner.
                snapshot_id = str(context.get("trusted_snapshot_id") or "latest")
                requests.append({"source_system": "quant", "operation": "market_snapshot", "evidence_type": evidence_type, "subject_key": "market", "snapshot_id": snapshot_id, "params": {"snapshot_id": snapshot_id}})
            elif evidence_type == "MARKET_REGIME":
                requests.append({"source_system": "quant", "operation": "market_regime", "evidence_type": evidence_type, "subject_key": "market", "params": {"as_of": decision_time.isoformat()}})
            elif evidence_type == "SECTOR_STRENGTH":
                requests.append({"source_system": "quant", "operation": "sector_strength", "evidence_type": evidence_type, "subject_key": "market", "params": {"as_of": decision_time.isoformat()}})
            elif evidence_type.startswith("TECHNICAL"):
                requests.extend({"source_system": "quant", "operation": "technical_evidence", "evidence_type": evidence_type, "subject_key": subject, "params": {"symbol": subject, "as_of": decision_time.isoformat()}} for subject in subjects)
            elif evidence_type.startswith("FACTOR"):
                # Factor IDs are owned by the trusted request injector.  A
                # public stock symbol is never reused as a factor identifier.
                factor_ids = []
                requests.extend({"source_system": "factor", "operation": "factor_evidence", "evidence_type": evidence_type, "subject_key": str(factor_id), "params": {"factor_id": str(factor_id)}} for factor_id in factor_ids)
            elif evidence_type in {"PORTFOLIO_POSITION", "PORTFOLIO_EXPOSURE"}:
                requests.append({"source_system": "quant", "operation": "portfolio_snapshot", "evidence_type": evidence_type, "subject_key": context.get("account_id", "portfolio"), "params": {"account_id": context.get("account_id")}})
            elif evidence_type == "PORTFOLIO_RISK":
                requests.append({"source_system": "quant", "operation": "portfolio_risk", "evidence_type": evidence_type, "subject_key": context.get("account_id", "portfolio"), "params": {"account_id": context.get("account_id")}})
            elif evidence_type in {"KNOWLEDGE_CLAIM", "CATALYST", "VALUATION_FACT", "EARNINGS_FACT", "RISK_EVENT"}:
                requests.extend({"source_system": "content", "operation": "content_search", "evidence_type": evidence_type, "subject_key": str(subject), "params": {"query": str(subject)}} for subject in subjects)
        return requests

    @staticmethod
    def _formal_governance_seed(raw: dict[str, Any], proposal: InvestmentProposalV2, artifacts: list[SpecialistArtifact]) -> dict[str, Any]:
        risk = next((item for item in artifacts if item.specialist == SpecialistRole.RISK), None)
        risk_conclusion = dict(risk.conclusion if risk else {})
        risk_payload = dict(risk_conclusion.get("risk") or {})
        veto = bool(risk_conclusion.get("veto") or risk_payload.get("veto") or risk_payload.get("risk_veto") or (risk is not None and risk.status == SpecialistStatus.FAILED))
        return {"proposal": proposal, "resolution": {"vetoed": veto, "veto_reasons": ["RISK_VETO"] if veto else []}, "final_decision": {"action": "VETO" if veto else proposal.action, "approved": not veto, "approved_weight": float(proposal.target_weight or 0.0), "vetoed": veto, "veto_reasons": ["RISK_VETO"] if veto else [], "rejections": [], "adjustments": []}}

    def _run_bundle_specialists(self, task_type: str, objective: str, context: dict[str, Any], decision_time: datetime, bundle: DecisionInputBundle) -> list[SpecialistArtifact]:
        registry = _BundleToolRegistry(bundle)
        specialist_context = {
            **self._safe_formal_context(context), "bundle_id": bundle.bundle_id, "bundle_hash": bundle.bundle_hash,
            "evidence_refs": [item.evidence_id for item in bundle.evidence],
            "candidate_symbols": list(context.get("subjects") or []), "symbols": list(context.get("subjects") or []),
            "universe": list(context.get("subjects") or []), "candidates": list(context.get("subjects") or []),
        }
        specialists = {
            AgentRole.MARKET: MarketSpecialist(registry, specialist_context),
            AgentRole.RESEARCH: ResearchSpecialist(registry, specialist_context),
            AgentRole.TECHNICAL: TechnicalSpecialist(registry, specialist_context),
            AgentRole.FACTOR: FactorSpecialist(registry, specialist_context, factor_client=_BundleFactorClient(bundle)),
            AgentRole.PORTFOLIO: PortfolioSpecialist(registry, specialist_context),
            AgentRole.RISK: RiskSpecialist(registry, specialist_context),
        }
        graph = build_daily_market_decision_graph(objective, tool_budget=5, token_budget=1000)
        requested_skill = str(context.get("skill") or task_type)
        active_skill = next((item for item in load_skills() if item.slug == requested_skill), None)
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
        artifacts: list[SpecialistArtifact] = []
        roles = (AgentRole.MARKET, AgentRole.RESEARCH, AgentRole.TECHNICAL, AgentRole.FACTOR, AgentRole.PORTFOLIO, AgentRole.RISK)
        if required_roles:
            roles = tuple(role for role in roles if role.name in required_roles or role.value.upper() in required_roles)
        for role in roles:
            item = by_role.get(role.name) or by_role.get(role.value)
            if item is not None:
                artifacts.append(self._normalize_bundle_artifact(SpecialistArtifact.model_validate(item), bundle))
                continue
            task = next((value for value in graph.tasks.values() if value.assigned_agent == role), None)
            artifacts.append(SpecialistArtifact(
                task_id=task.task_id if task else f"{role.value.lower()}-missing", specialist=role,
                status=SpecialistStatus.FAILED, conclusion={}, warnings=["SPECIALIST_FAILED"], unknowns=["SPECIALIST_FAILED"],
                confidence=0.0, tool_usage=ToolUsage(calls=0),
            ))
        return artifacts

    @staticmethod
    def _normalize_bundle_artifact(artifact: SpecialistArtifact, bundle: DecisionInputBundle) -> SpecialistArtifact:
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

    def _formal_execute(self, task_type: str, objective: str, context: dict[str, Any], decision_time: datetime, bundle: DecisionInputBundle, specialist_artifacts: list[SpecialistArtifact]) -> dict[str, Any]:
        if self.agent_enabled():
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
            result = run_formal(
                user_query=objective, context=formal_context, force_skill=context.get("skill"),
            )
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

    def _missing_context(self, bundle: DecisionInputBundle, context: dict[str, Any], governed: dict[str, Any], proposal: InvestmentProposalV2) -> list[str]:
        bad = {item.system.value: item for item in bundle.dependency_status if str(item.status) in {"UNAVAILABLE", "STALE"}}
        required = {str(item) for item in (context.get("required_dependencies") or [])}
        reasons = [f"{system}_dependency_{str(item.status).lower()}" for system, item in bad.items() if system in {"quant", "market"} or system in required]
        target_requested = bool(context.get("target_weight") is not None or context.get("requires_portfolio") or (proposal.action in {"BUY", "SELL", "INCREASE", "REDUCE", "EXIT"} and (proposal.target_weight is not None or proposal.weight_delta is not None)))
        if target_requested and not any(item.evidence_type.value.startswith("PORTFOLIO") for item in bundle.evidence):
            reasons.append("portfolio_evidence_required")
        return sorted(set(reasons))

    @staticmethod
    def _formal_quality(bundle: DecisionInputBundle, governed: dict[str, Any], missing: list[str], context: dict[str, Any], specialist_artifacts: list[SpecialistArtifact], proposal: InvestmentProposalV2) -> dict[str, Any]:
        domains = {"market": 0.0, "research": 0.0, "technical": 0.0, "factor": 0.0, "portfolio": 0.0, "risk": 0.0}
        for item in bundle.evidence:
            name = item.evidence_type.value.lower()
            if name.startswith("market") or name in {"sector_strength", "liquidity", "technical_signal", "technical_profile"}:
                domains["market"] = max(domains["market"], 1.0)
            if name.startswith("factor"):
                domains["factor"] = 1.0
            if name.startswith("knowledge") or name in {"catalyst", "earnings_fact", "valuation_fact"}:
                domains["research"] = 1.0
            if name.startswith("portfolio"):
                domains["portfolio"] = 1.0
            if name == "risk_event":
                domains["risk"] = 1.0
        statuses = [{"system": item.system.value, "status": item.status.value} for item in bundle.dependency_status]
        artifacts = [item.model_dump(mode="json") if isinstance(item, SpecialistArtifact) else item for item in specialist_artifacts]
        synthesis = synthesize_evidence(bundle.evidence, dependencies=bundle.dependency_status, specialist_artifacts=artifacts)
        coverage = synthesis.coverage.model_dump(mode="json")
        assessment = compute_decision_quality_v2(
            coverage=coverage, dependency_status=statuses, specialist_artifacts=artifacts,
            target_weight_requested=bool(proposal.target_weight is not None or proposal.weight_delta is not None),
            evidence_quality=[item.quality_status.value for item in bundle.evidence],
        )
        payload = assessment.model_dump(mode="json")
        payload["degraded_reasons"] = sorted(set(payload.get("degraded_reasons") or []) | set(missing))
        payload["unknowns"] = sorted(set(payload.get("unknowns") or []) | set(missing))
        return payload

    @staticmethod
    def _report_text(report: Any) -> str:
        if isinstance(report, str) and report.strip():
            return report
        if isinstance(report, dict):
            text = report.get("summary") or report.get("text")
            if isinstance(text, str) and text.strip():
                return text
        return "Formal decision produced from the frozen input bundle."

    @staticmethod
    def _bundle_before_patch(bundle: DecisionInputBundle, previous_hash: str) -> DecisionInputBundle:
        # The resolver returns the new bundle.  Persistence validates the patch
        # against the prior immutable row, so reconstructing only the identity
        # is intentionally avoided; callers that need this hook should provide
        # a repository with explicit patch lineage support.
        return bundle.model_copy(update={"bundle_hash": previous_hash})

    def _proposal_v2(self, raw: dict[str, Any], governed: dict[str, Any], bundle: DecisionInputBundle, decision_id: str) -> InvestmentProposalV2:
        if self.agent_enabled() and isinstance(raw.get("proposal"), dict):
            try:
                return InvestmentProposalV2.model_validate(raw["proposal"])
            except Exception as exc:
                raise ValueError("MODEL_PROPOSAL_INVALID: expected InvestmentProposalV2") from exc
        p = governed.get("proposal") if isinstance(governed, dict) else None
        if p is None:
            value = dict(raw.get("proposal") or {})
            p = InvestmentProposal(
                symbol=str(value.get("symbol") or ""), action=str(value.get("action") or "HOLD").upper(),
                proposed_weight=float(value.get("proposed_weight") or 0.0), confidence=float(value.get("confidence") or 0.0),
                thesis_refs=list(value.get("thesis_refs") or []), evidence_count=int(value.get("evidence_count") or 0),
            )
        action = p.action if p.action in {"BUY", "SELL", "HOLD", "INCREASE", "REDUCE", "EXIT", "WATCH"} else "HOLD"
        target = p.proposed_weight if action not in {"HOLD", "WATCH"} else None
        return InvestmentProposalV2.build(
            proposal_id=f"proposal-{decision_id}", subject_type="SYMBOL" if p.symbol else "PORTFOLIO", subject_key=p.symbol or None,
            action=action, target_weight=target, weight_delta=None, confidence=p.confidence,
            horizon=DecisionHorizon(period="decision"), thesis=[], catalysts=[], entry_conditions=[], invalidation_conditions=[], expected_risks=[],
            evidence_refs=[item.evidence_id for item in bundle.evidence], specialist_artifact_refs=[], unknowns=[],
            generated_by=ModelIdentity(provider=str(raw.get("provider") or "unavailable"), model=str(raw.get("model") or "unavailable"), model_version=str(raw.get("model_version") or "unavailable")),
        )

    def _policy_v2(self, governed: dict[str, Any], decision_id: str) -> PolicyEvaluation:
        decision = governed["policy_decision"]
        checks = []
        for item in decision.checks or []:
            value = item.__dict__ if hasattr(item, "__dict__") else dict(item)
            checks.append({"rule_id": str(value.get("rule") or "POLICY"), "rule_version": decision.policy_version, "passed": bool(value.get("passed", True)), "severity": "INFO" if value.get("passed", True) else "REJECT", "input_snapshot": {}, "original_value": None, "adjusted_value": None, "reason_code": str(value.get("rule") or "POLICY"), "reason": str(value.get("reason") or value.get("rule") or "deterministic policy check")})
        if not checks:
            checks = [{"rule_id": "POLICY", "rule_version": decision.policy_version, "passed": bool(decision.approved), "severity": "INFO" if decision.approved else "REJECT", "input_snapshot": {}, "original_value": None, "adjusted_value": None, "reason_code": "POLICY", "reason": "deterministic policy evaluation"}]
        return PolicyEvaluation.build(policy_result_id=f"policy-{decision_id}", policy_version=decision.policy_version, checks=checks, approved=bool(decision.approved), original_value=governed["proposal"].to_dict(), adjusted_value=decision.approved_weight)

    def _evaluate_policy_v2(self, raw: dict[str, Any], governed: dict[str, Any], proposal: InvestmentProposalV2, bundle: DecisionInputBundle, specialist_artifacts: list[SpecialistArtifact], request_context: dict[str, Any]) -> PolicyEvaluation:
        context = self._policy_context_from_bundle(raw, proposal, bundle, specialist_artifacts, request_context)
        evaluated = self.policy_engine.evaluate(proposal, context)
        if isinstance(evaluated, PolicyEvaluation):
            return evaluated
        return self._policy_v2(governed, str(uuid4()))

    def _policy_context_from_bundle(self, raw: dict[str, Any], proposal: InvestmentProposalV2, bundle: DecisionInputBundle, specialist_artifacts: list[SpecialistArtifact], request_context: dict[str, Any]) -> PolicyContext:
        synthesis = synthesize_evidence(bundle.evidence, dependencies=bundle.dependency_status, specialist_artifacts=[item.model_dump(mode="json") for item in specialist_artifacts])
        evidence_payloads = [dict(item.payload) for item in bundle.evidence]
        portfolio = next((item for item in evidence_payloads if any(key in item for key in ("gross_exposure", "net_exposure", "positions", "portfolio_available"))), {})
        risk_artifact = next((item for item in specialist_artifacts if item.specialist == SpecialistRole.RISK), None)
        risk_conclusion = dict(risk_artifact.conclusion if risk_artifact else {})
        risk_payload = dict(risk_conclusion.get("risk") or {})
        dependencies = {item.system.value: item.status.value for item in bundle.dependency_status}
        security_rows = [item for item in bundle.evidence if item.source_system == SourceSystem.QUANT and item.subject_key == proposal.subject_key and isinstance(item.payload, dict)]
        def _security_fact(name: str) -> bool | None:
            values = [item.payload[name] for item in security_rows if isinstance(item.payload.get(name), bool)]
            if len(set(values)) > 1:
                raise ValueError(f"SECURITY_FACT_CONFLICT:{name}")
            return values[0] if values else None
        is_st = _security_fact("is_st")
        is_suspended = _security_fact("is_suspended")
        liquidity = _security_fact("liquidity_ok")
        security_is_st = {proposal.subject_key: is_st} if proposal.subject_key and is_st is not None else {}
        security_is_suspended = {proposal.subject_key: is_suspended} if proposal.subject_key and is_suspended is not None else {}
        dependency_required = {}
        skill_required: list[str] = []
        skill_slug = str(request_context.get("skill") or raw.get("selected_skill") or proposal.subject_type).strip()
        skill = next((item for item in load_skills() if item.slug == skill_slug), None)
        required_domains = ["market"]
        if skill is not None:
            required_domains = ["market"]
            required_domains.extend("factor" for item in skill.required_evidence if str(item).startswith("FACTOR"))
            required_domains.extend("risk" for item in skill.required_evidence if str(item) in {"PORTFOLIO_RISK", "RISK_EVENT"})
            skill_required.extend("factor" for item in skill.required_evidence if str(item).startswith("FACTOR"))
            skill_required.extend("content" for item in skill.required_evidence if str(item) in {"KNOWLEDGE_CLAIM", "CATALYST", "VALUATION_FACT", "EARNINGS_FACT"})
        referenced_evidence = [item for item in bundle.evidence if item.evidence_id in set(proposal.evidence_refs)]
        liquidity_map = {proposal.subject_key: liquidity} if proposal.subject_key and liquidity is not None else {}
        return PolicyContext(
            portfolio_drawdown_mode=bool(portfolio.get("drawdown_mode", False)),
            restricted_universe=list(portfolio.get("restricted_universe") or []),
            existing_weights={str(k): float(v) for k, v in (portfolio.get("existing_weights") or {}).items()},
            gross_exposure=portfolio.get("gross_exposure"), net_exposure=portfolio.get("net_exposure"),
            portfolio_available=bool(portfolio) if proposal.target_weight is not None or proposal.weight_delta is not None else True,
            portfolio_snapshot_at=next((item.as_of for item in bundle.evidence if item.evidence_type.value.startswith("PORTFOLIO")), None),
            portfolio_snapshot_fresh=bool(portfolio and all(item.quality_status.value not in {"STALE", "REJECTED"} for item in bundle.evidence if item.evidence_type.value.startswith("PORTFOLIO"))),
            portfolio_snapshot_freshness_seconds=max([max(0.0, (bundle.decision_time - item.available_at).total_seconds()) for item in bundle.evidence if item.evidence_type.value.startswith("PORTFOLIO")] or [0.0]),
            dependency_health=dependencies, dependency_status=dependencies,
            evidence_fresh=True if not proposal.evidence_refs else bool(referenced_evidence and all(item.quality_status.value not in {"STALE", "REJECTED"} and item.available_at <= bundle.decision_time for item in referenced_evidence)),
            evidence_freshness_seconds=max([max(0.0, (bundle.decision_time - item.available_at).total_seconds()) for item in referenced_evidence] or [0.0]),
            risk_veto=bool(risk_conclusion.get("veto") or risk_payload.get("veto") or risk_payload.get("risk_veto") or (risk_artifact is not None and risk_artifact.status == SpecialistStatus.FAILED)),
            risk_veto_reason=str(risk_payload.get("reason") or ""),
            domain_coverage=synthesis.coverage.model_dump(mode="json"),
            required_domains=required_domains, required_dependencies=list(skill_required),
            skill_required_dependencies=skill_required, dependency_required=dependency_required,
            security_facts_verified={proposal.subject_key: bool(security_rows and proposal.subject_key in liquidity_map and liquidity_map[proposal.subject_key])} if proposal.subject_key else {},
            security_is_st=security_is_st, security_is_suspended=security_is_suspended,
            liquidity_ok=liquidity_map,
        )

    def _final_v2(self, governed: dict[str, Any], proposal: InvestmentProposalV2, policy: PolicyEvaluation, bundle: DecisionInputBundle, decision_id: str, snapshot_id: str, quality: dict[str, Any]) -> FinalInvestmentDecision:
        final = governed["final_decision"]
        veto = final["action"] in {"VETO", "REJECT"} or not final.get("approved", False) or not policy.approved
        approved_weight = policy.adjusted_value if isinstance(policy.adjusted_value, (int, float)) else final.get("approved_weight", 0.0)
        action = proposal.action if proposal.action in {"BUY", "SELL", "HOLD", "INCREASE", "REDUCE", "EXIT", "WATCH"} else "HOLD"
        return FinalInvestmentDecision.build(
            decision_id=decision_id, decision_time=bundle.decision_time, valid_from=bundle.decision_time,
            subject_type=proposal.subject_type, subject_key=proposal.subject_key, decision_action="VETO" if veto else ("APPROVE_WITH_ADJUSTMENT" if approved_weight != (proposal.target_weight or 0.0) else "APPROVE"),
            investment_action=action, target_weight=None if veto or action in {"HOLD", "WATCH"} else approved_weight, weight_delta=None,
            confidence=proposal.confidence, decision_quality=quality["level"], rationale=list(final.get("veto_reasons") or []) or ["deterministic policy result"], evidence_refs=list(proposal.evidence_refs), proposal_id=proposal.proposal_id, policy_result_id=policy.policy_result_id, invalidation_conditions=list(proposal.invalidation_conditions), bundle_id=bundle.bundle_id, snapshot_id=snapshot_id,
        )

    def _snapshot_v3(self, *, snapshot_id: str, decision_id: str, decision_time: datetime, task_type: str, context: dict[str, Any], bundle: DecisionInputBundle, statuses: list[Any], raw: dict[str, Any], proposal: InvestmentProposalV2, policy: PolicyEvaluation, final: FinalInvestmentDecision, quality: dict[str, Any], patches: list[Any] | None = None) -> DecisionSnapshotV3:
        evidence_payloads = {item.evidence_id: item.model_dump(mode="json") for item in bundle.evidence}
        evidence_hashes = {key: canonical_hash(value) for key, value in evidence_payloads.items()}
        content_snapshot_ids = sorted({str(item.snapshot_id or item.payload.get("content_snapshot_id")) for item in bundle.evidence if item.source_system == SourceSystem.CONTENT and (item.snapshot_id or item.payload.get("content_snapshot_id"))})
        market_snapshot_ids = sorted({str(item.snapshot_id or item.payload.get("snapshot_id")) for item in bundle.evidence if item.source_system == SourceSystem.QUANT and item.evidence_type.value == "MARKET_SNAPSHOT" and (item.snapshot_id or item.payload.get("snapshot_id"))})
        artifacts = list(raw.get("specialist_artifacts") or [])
        if artifacts:
            parsed_artifacts = [item if isinstance(item, SpecialistArtifact) else SpecialistArtifact.model_validate(item) for item in artifacts]
            artifact_refs = [item.artifact_id for item in parsed_artifacts]
            artifact_hashes = {item.artifact_id: item.artifact_hash for item in parsed_artifacts}
            artifact_payloads = {item.artifact_id: item.model_dump(mode="json", exclude={"artifact_hash"}) for item in parsed_artifacts}
        else:
            artifact_refs = []
            artifact_hashes = {}
            artifact_payloads = {}
        bundle_payload = bundle.model_dump(mode="json")
        # The frozen bundle is authoritative after a lazy patch; never let a
        # stale local status list diverge from the replay payload.
        dependency_states = {item.system.value: {"status": item.status.value, "checked_at": item.checked_at.isoformat(), "contract_version": item.contract_version, "service_version": item.service_version, "snapshot_id": item.snapshot_id, "reason_codes": list(item.reason_codes)} for item in bundle.dependency_status}
        for system in ("quant", "factor", "content"):
            dependency_states.setdefault(system, {"status": "UNAVAILABLE", "reason_codes": ["NOT_COLLECTED"]})
        model_meta = raw.get("model_identity") if isinstance(raw.get("model_identity"), dict) else {}
        usage_meta = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        input_tokens = int(usage_meta.get("input_tokens", raw.get("input_tokens", 0)) or 0)
        output_tokens = int(usage_meta.get("output_tokens", raw.get("output_tokens", 0)) or 0)
        total_tokens = int(usage_meta.get("total_tokens", raw.get("total_tokens", raw.get("tokens", 0))) or 0)
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens
        actual_cost = usage_meta.get("cost", raw.get("cost", usage_meta.get("estimated_cost", raw.get("estimated_cost", 0.0))))
        return DecisionSnapshotV3(
            snapshot_id=snapshot_id, decision_id=decision_id, decision_time=decision_time,
            runtime={"runtime_mode": raw.get("runtime_mode") or ("PRIMARY_AGENT" if self.agent_enabled() else "DETERMINISTIC_FALLBACK"), "workflow_version": FORMAL_WORKFLOW_VERSION, "fallback_used": not self.agent_enabled(), "trace_id": str(context.get("trace_context", {}).get("trace_id")), "decision_id": decision_id, "bundle_id": bundle.bundle_id, "snapshot_id": snapshot_id},
            input_bundle=SnapshotBundleRef(bundle_id=bundle.bundle_id, bundle_hash=bundle.bundle_hash, schema_version=bundle.schema_version, payload=bundle_payload),
            dependencies={"states": dependency_states, **dependency_states, "bundle_id": bundle.bundle_id, "bundle_hash": bundle.bundle_hash, "patches": [{"patch_id": item.patch_id, "previous_hash": item.previous_hash, "new_hash": item.new_hash} for item in (patches or [])]},
            evidence={"refs": [{"id": key} for key in evidence_payloads], "hashes": evidence_hashes, "payloads": evidence_payloads},
            specialists={"artifact_refs": artifact_refs, "artifact_hashes": artifact_hashes, "payloads": artifact_payloads},
            model={"provider": str(raw.get("provider") or model_meta.get("provider") or proposal.generated_by.provider), "model": str(raw.get("model") or model_meta.get("model") or proposal.generated_by.model), "model_version": str(raw.get("model_version") or model_meta.get("model_version") or proposal.generated_by.model_version or "unavailable"), "prompt_version": str(raw.get("prompt_version") or model_meta.get("prompt_version") or "unavailable")},
            skill=self._skill_snapshot(raw, context, task_type),
            proposal={"proposal_id": proposal.proposal_id, "proposal_hash": proposal.proposal_hash, "payload": proposal.model_dump(mode="json", exclude={"proposal_hash"})},
            conflicts=list(raw.get("conflicts") or []), risk={"veto": final.decision_action == "VETO", "reasons": list(final.rationale), "risk_rule_version": str(getattr(self.policy_engine, "risk_rule_version", None) or "policy-risk.v2")},
            policy={"policy_result_id": policy.policy_result_id, "policy_version": policy.policy_version, "checks": policy.model_dump(mode="json")["checks"], "approved": policy.approved, "original_value": policy.original_value, "adjusted_value": policy.adjusted_value, "result_hash": policy.result_hash},
            output={"decision_id": decision_id, "final_decision_id": final.decision_id, "bundle_id": bundle.bundle_id, "proposal_id": proposal.proposal_id, "policy_result_id": policy.policy_result_id, "final_decision": final.model_dump(mode="json", exclude={"decision_hash"}), "final_decision_hash": canonical_hash(final.model_dump(mode="json", exclude={"decision_hash"}))},
            lineage=[
                {"type": "BUNDLE", "id": bundle.bundle_id, "hash": bundle.bundle_hash},
                *[{"type": "BUNDLE_PATCH", "id": item.patch_id, "hash": item.new_hash} for item in (patches or [])],
                *[{"type": "EVIDENCE", "id": key, "hash": value} for key, value in evidence_hashes.items()],
                *[{"type": "CONTENT_SNAPSHOT", "id": snapshot_id} for snapshot_id in content_snapshot_ids],
                *[{"type": "MARKET_SNAPSHOT", "id": snapshot_id} for snapshot_id in market_snapshot_ids],
                *[{"type": "SPECIALIST_ARTIFACT", "id": key, "hash": value} for key, value in artifact_hashes.items()],
                {"type": "PROPOSAL", "id": proposal.proposal_id, "hash": proposal.proposal_hash},
                {"type": "POLICY", "id": policy.policy_result_id, "hash": policy.result_hash},
                {"type": "FINAL_DECISION", "id": final.decision_id, "hash": final.decision_hash},
                {"type": "DECISION", "id": decision_id, "hash": final.decision_hash},
                {"type": "TRACE", "id": str(context.get("trace_context", {}).get("trace_id")), "hash": canonical_hash(context.get("trace_context", {}))},
            ], decision_quality=quality,
            usage={"tool_calls": int(usage_meta.get("tool_calls", raw.get("tool_calls") or (sum(item.tool_usage.calls for item in parsed_artifacts) if artifacts else 0))), "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens, "tokens": total_tokens, "latency_ms": int(usage_meta.get("latency_ms", raw.get("latency_ms", 0)) or 0), "specialist_tool_calls": sum(item.tool_usage.calls for item in parsed_artifacts) if artifacts else 0, "cost": actual_cost, "estimated_cost": usage_meta.get("estimated_cost", actual_cost)},
        )

    def _decision_time(self, value: datetime | None) -> datetime:
        result = value or self._clock_now()
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return result

    @staticmethod
    def _skill_snapshot(raw: dict[str, Any], context: dict[str, Any], task_type: str) -> dict[str, Any]:
        slug = str(raw.get("selected_skill") or context.get("skill") or task_type)
        skill = next((item for item in load_skills() if item.slug == slug), None)
        if skill is None:
            raise ValueError(f"SKILL_NOT_FOUND: {slug}")
        return {"slug": skill.slug, "version": str(skill.version), "contract_hash": str(skill.skill_contract_hash), "markdown_hash": str(skill.skill_markdown_hash), "required_specialists": list(skill.required_specialists), "required_evidence": list(skill.required_evidence)}

    def _clock_now(self) -> datetime:
        value = self.clock() if callable(self.clock) else self.clock.now() if self.clock is not None and hasattr(self.clock, "now") else datetime.now(UTC)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value

    # ---------------------------------------------------------------- pipeline
    def _run_pipeline(
        self,
        *,
        task_type: str,
        role: AgentRole,
        objective: str,
        execute: Callable[[], dict],
        query: str,
        subject: str | None,
        as_of: date | None = None,
        emit: Callable[[str, dict], None] | None = None,
    ) -> dict:
        mode, fallback_reason = self.resolve_runtime_mode()
        supervised = self._run_supervised(task_type=task_type, role=role, objective=objective, execute=execute, as_of=as_of)
        payload = supervised["payload"]
        governed = self._govern(payload)
        persisted = self._persist(
            objective=query,
            payload=payload,
            governed=governed,
            mode=mode,
            fallback_reason=fallback_reason,
            task_type=task_type,
            subject=subject,
            agent_run_id=supervised.get("agent_run_id"),
            decision_quality=supervised.get("decision_quality"),
        )
        payload.update(self._actionable_segment(governed, persisted, mode))
        if emit:
            emit("done", payload)
        return payload

    def _run_narrative(
        self, *, task_type: str, role: AgentRole, objective: str, execute: Callable[[], dict],
        query: str, subject: str | None, as_of: date | None = None,
        emit: Callable[[str, dict], None] | None = None,
    ) -> dict:
        """Compatibility adapter with no formal decision/persistence semantics."""
        mode, fallback_reason = self.resolve_runtime_mode()
        try:
            raw = execute()
        except Exception as exc:  # narrative endpoints must remain non-actionable
            raw = {"report": f"Narrative adapter unavailable: {exc}"}
            mode = RuntimeMode.DEGRADED_AGENT
            fallback_reason = type(exc).__name__
        report = self._report_text(raw.get("report") if isinstance(raw, dict) else raw)
        payload = {
            "orchestration": "legacy-narrative-adapter",
            "runtime_mode": mode.value if hasattr(mode, "value") else str(mode),
            "fallback_reason": fallback_reason,
            "actionable": False,
            "report": report,
            "task_type": task_type,
        }
        if emit:
            emit("done", payload)
        return payload

    def _run_supervised(
        self,
        *,
        task_type: str,
        role: AgentRole,
        objective: str,
        execute: Callable[[], dict],
        as_of: date | None,
    ) -> dict:
        """Supervisor/TaskGraph 将执行适配器包装为 specialist artifact（单一决策链路）。"""
        as_of_dt = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC) if isinstance(as_of, date) else self._clock_now()
        graph = TaskGraph()
        graph.add_task(AgentTask(task_type=task_type, assigned_agent=role, objective=objective, as_of=as_of_dt))

        def specialist(task: AgentTask, _shared) -> AgentArtifact:
            adapter_payload = execute()
            proposal_payload = adapter_payload.get("proposal") if isinstance(adapter_payload, dict) else None
            confidence = float((proposal_payload or {}).get("confidence") or 0.0)
            return AgentArtifact(
                agent=role,
                task_id=task.task_id,
                status=TaskStatus.SUCCESS,
                conclusion={"payload": adapter_payload},
                confidence=max(0.0, min(confidence, 1.0)),
            )

        result = Supervisor({role: specialist}).run(graph)
        artifacts = result.get("artifacts") or []
        payload = ((artifacts[0] or {}).get("conclusion") or {}).get("payload") if artifacts else {}
        return {
            "payload": payload if isinstance(payload, dict) else {},
            "agent_run_id": result.get("agent_run_id"),
            "decision_quality": result.get("decision_quality"),
            "errors": result.get("errors") or [],
        }

    # ---------------------------------------------------------------- governance
    def _govern(self, payload: dict) -> dict:
        """Conflict → Risk VETO → Proposal → Policy → Suitability → Final Decision。"""
        payload = payload if isinstance(payload, dict) else {}
        proposal_payload = dict(payload.get("proposal") or {})
        proposal = InvestmentProposal(
            symbol=str(proposal_payload.get("symbol") or payload.get("symbol") or ""),
            action=str(proposal_payload.get("action") or "HOLD").upper(),
            proposed_weight=float(proposal_payload.get("proposed_weight") or 0.0),
            confidence=float(proposal_payload.get("confidence") or 0.0),
            thesis_refs=list(proposal_payload.get("thesis_refs") or []),
            sector=proposal_payload.get("sector") or payload.get("sector"),
            theme=proposal_payload.get("theme") or payload.get("theme"),
            evidence_count=int(proposal_payload.get("evidence_count") or 0),
            factor_coverage=float(proposal_payload.get("factor_coverage") or 1.0),
            liquidity_ok=bool(proposal_payload.get("liquidity_ok", True)),
            is_st=bool(proposal_payload.get("is_st", False)),
            is_suspended=bool(proposal_payload.get("is_suspended", False)),
        )
        context = PolicyContext(
            portfolio_drawdown_mode=bool(payload.get("portfolio_drawdown_mode", False)),
            restricted_universe=list(payload.get("restricted_universe") or []),
            existing_weights={str(k): float(v) for k, v in (payload.get("existing_weights") or {}).items()},
        )
        conflicts = [dict(item) for item in (payload.get("conflicts") or []) if isinstance(item, dict)]
        risk = dict(payload.get("risk") or {})
        if risk.get("veto"):
            # Risk VETO 必须覆盖任何 LLM BUY。
            conflicts.append(
                {
                    "type": "RISK_CONFLICT",
                    "dimension": str(risk.get("reason") or "RISK_VETO"),
                    "options": [{"agent": "RiskAgent", "value": "VETO", "veto": True}],
                    "risk_veto": True,
                }
            )
        resolution = resolve_conflicts_v2(conflicts)
        decision = self.policy_engine.evaluate(proposal, context)
        vetoed = bool(resolution.get("vetoed"))

        suitability: dict | None = None
        profile_payload = payload.get("investor_profile")
        if isinstance(profile_payload, dict) and profile_payload:
            profile = InvestorProfile(
                risk_level=str(profile_payload.get("risk_level") or "BALANCED"),
                investment_horizon_years=float(profile_payload.get("investment_horizon_years") or 3.0),
                liquidity_need=str(profile_payload.get("liquidity_need") or "MEDIUM"),
                max_drawdown_tolerance=float(profile_payload.get("max_drawdown_tolerance") or 0.15),
                allowed_markets=tuple(profile_payload.get("allowed_markets") or ("CN_A",)),
                allowed_products=tuple(profile_payload.get("allowed_products") or ("EQUITY",)),
            )
            recommendation = Recommendation(
                symbol=proposal.symbol or "UNKNOWN",
                action=proposal.action,
                weight=proposal.proposed_weight,
                market=str(profile_payload.get("market") or "CN_A"),
                product=str(profile_payload.get("product") or "EQUITY"),
                risk_rating=str(profile_payload.get("product_risk_rating") or "BALANCED"),
                expected_max_drawdown=float(profile_payload.get("expected_max_drawdown") or 0.10),
                holding_horizon_years=float(profile_payload.get("holding_horizon_years") or 1.0),
                liquidity_profile=str(profile_payload.get("product_liquidity_profile") or "HIGH"),
            )
            suitability = evaluate_suitability(profile, recommendation)

        approved = bool(decision.approved) and not vetoed
        approved_weight = decision.approved_weight if approved else 0.0
        if vetoed:
            action = "VETO"
        elif not approved:
            action = "REJECT"
        elif suitability is not None and not suitability.get("suitable"):
            # Suitability FAIL 时不得输出不符合策略的 actionable advice。
            action, approved, approved_weight = "REJECT", False, 0.0
        else:
            action = proposal.action
            if suitability is not None:
                approved_weight = min(approved_weight, float(suitability.get("approved_weight") or approved_weight))
        return {
            "proposal": proposal,
            "policy_decision": decision,
            "resolution": resolution,
            "suitability": suitability,
            "final_decision": {
                "action": action,
                "approved": approved,
                "approved_weight": round(float(approved_weight), 6),
                "vetoed": vetoed,
                "veto_reasons": list(resolution.get("veto_reasons") or []),
                "rejections": list(decision.rejections),
                "adjustments": list(decision.adjustments),
                "suitability": suitability,
            },
        }

    # ---------------------------------------------------------------- persistence
    def _persist(
        self,
        *,
        objective: str,
        payload: dict,
        governed: dict,
        mode: RuntimeMode,
        fallback_reason: str | None,
        task_type: str,
        subject: str | None,
        agent_run_id: str | None,
        decision_quality: str | None,
    ) -> dict:
        """DecisionService.save_decision() → DecisionSnapshot v2（fallback 也生成）。"""
        proposal: InvestmentProposal = governed["proposal"]
        decision = governed["policy_decision"]
        final_decision = governed["final_decision"]

        market_snapshot_id = payload.get("market_snapshot_id")
        content_segment, content_snapshot_ids = self._content_lineage(payload)
        tool_segment = self._record_tool_result(
            task_type=task_type,
            objective=objective,
            payload=payload,
            agent_run_id=agent_run_id,
            snapshot_refs=([str(market_snapshot_id)] if market_snapshot_id else []) + content_snapshot_ids,
        )

        snapshot_segments = {
            "market": {"snapshot_id": market_snapshot_id, "data_version": payload.get("market_data_version")},
            "content": content_segment,
            "factor": {
                "factor_set_version": payload.get("factor_set_version"),
                "research_experiment_id": payload.get("research_experiment_id"),
            },
            "strategy": {"strategy_id": payload.get("selected_skill") or task_type, "strategy_version": payload.get("selected_skill") or task_type},
            "runtime": build_runtime_segment(mode, fallback_reason=fallback_reason, supervisor_version=DECISION_RUNTIME_VERSION),
            "proposal": proposal.to_dict(),
            "policy": {
                **decision.to_dict(),
                "risk_veto": final_decision["vetoed"],
                "veto_reasons": final_decision["veto_reasons"],
                "final_action": final_decision["action"],
            },
            "tools": tool_segment,
            "inputs": {
                "market_snapshot_ids": [market_snapshot_id] if market_snapshot_id else [],
                "content_snapshot_ids": content_snapshot_ids,
                "research_experiment_ids": [payload["research_experiment_id"]] if payload.get("research_experiment_id") else [],
                "factor_set_ids": [payload["factor_set_version"]] if payload.get("factor_set_version") else [],
            },
            "output": {"final_decision": final_decision["action"], "approved_weight": final_decision["approved_weight"]},
        }
        candidates = [{"symbol": subject, "confidence": proposal.confidence}] if subject and proposal.symbol else []
        result = self.decision_service.save_decision(
            query=objective,
            candidates=candidates,
            themes=[proposal.theme] if proposal.theme else [],
            sector=proposal.sector,
            market_regime=payload.get("market_regime"),
            agent_run_id=agent_run_id,
            supervisor_version=DECISION_RUNTIME_VERSION,
            participating_agents=[mode.value],
            decision_quality=decision_quality,
            decision_snapshot=snapshot_segments,
        )
        return result

    def _content_lineage(self, payload: dict) -> tuple[dict, list[str]]:
        """v3 content signal 的 content_snapshot_id 必须真实进入 lineage。"""
        response = payload.get("content_signal_response")
        items: list[dict] = []
        contract_version = CONTENT_FACTOR_SIGNAL_VERSION
        if isinstance(response, dict):
            contract_version = str(response.get("contract_version") or CONTENT_FACTOR_SIGNAL_VERSION)
            items = [item for item in (response.get("items") or []) if isinstance(item, dict)]
        snapshot_ids = sorted({str(item.get("content_snapshot_id")) for item in items if item.get("content_snapshot_id")})
        segment = {"signal_contract": contract_version}
        if snapshot_ids:
            segment["snapshot_id"] = snapshot_ids[0]
        return segment, snapshot_ids

    def _record_tool_result(
        self, *, task_type: str, objective: str, payload: dict, agent_run_id: str | None, snapshot_refs: list[str]
    ) -> dict:
        """tools 段引用持久化的 ToolResultSnapshot（EXACT_REPLAY 依赖）。"""
        snapshot = self.tool_results.record(
            tool_id=f"decision_runtime.{task_type}",
            tool_version=DECISION_RUNTIME_VERSION,
            request={"objective": objective},
            response=payload,
            agent_run_id=agent_run_id,
            snapshot_refs=snapshot_refs,
        )
        return {"tool_result_ids": [snapshot.tool_result_id], "tool_id": f"decision_runtime.{task_type}"}

    @staticmethod
    def _actionable_segment(governed: dict, persisted: dict, mode: RuntimeMode) -> dict:
        return {
            "decision_id": persisted.get("decision_id"),
            "decision_snapshot_id": persisted.get("decision_snapshot_id"),
            "runtime_mode": mode.value,
            "proposal": governed["proposal"].to_dict(),
            "policy": governed["policy_decision"].to_dict(),
            "final_decision": governed["final_decision"],
        }


__all__ = ["DecisionRuntime", "DECISION_RUNTIME_VERSION", "LEGACY_SIGNAL_CONTRACT_VERSION"]

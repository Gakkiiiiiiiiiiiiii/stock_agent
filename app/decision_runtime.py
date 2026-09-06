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
from typing import Any
from uuid import uuid4

from agent.contracts import (
    AgentArtifact,
    AgentRole,
    AgentTask,
    SpecialistArtifact,
    SpecialistStatus,
    TaskStatus,
)
from agent.decision_quality import compute_decision_quality_v2
from agent.evidence_synthesis import synthesize_evidence
from agent.supervisor import Supervisor
from agent.task_graph import TaskGraph
from app.adapters.local.decision_runtime import (
    DecisionInputBundleRepository,
    DecisionService,
    DecisionSnapshotRepository,
    InvestmentProposal,
    InvestorProfile,
    OutcomeService,
    PolicyContext,
    PolicyEngine,
    Recommendation,
    ReviewService,
    RuntimeMode,
    ToolResultRepository,
    build_runtime_segment,
    evaluate_suitability,
    resolve_conflicts_v2,
)
from app.adapters.local.specialists import SpecialistRunnerComponent
from app.application.analysis.service import AnalysisApplicationService
from app.application.decision.runtime_components import (
    BundleFreezeComponent,
    FormalDecisionCalculatorComponent,
    SnapshotBuilderComponent,
)
from app.claude_agent import ClaudeAgent
from app.fallback_orchestrator import LocalFallbackOrchestrator
from app.model_gateway.metrics import MetricsRecorder, global_metrics
from contracts.decision_input import DecisionInputBundle
from contracts.decision_snapshot import (
    DecisionSnapshotV3,
)
from services.evidence.bundle import (
    DecisionInputBundleBuilder,
    DecisionInputBundleResolver,
)
from services.evidence.gateway import EvidenceGateway

DECISION_RUNTIME_VERSION = "decision-runtime.v1"
FORMAL_WORKFLOW_VERSION = "decision-runtime.v3"
# v2 仅允许显式 legacy mode（只服务旧 Release lane），main 默认 v3。
LEGACY_SIGNAL_CONTRACT_VERSION = "content-factor-signal.v2"


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
        application_service: Any | None = None,
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
        self.outcome_provider = outcome_provider or OutcomeService(clock=clock)
        self.review_provider = review_provider or ReviewService(clock=clock)
        self.metrics = metrics or global_metrics()
        # Compatibility façade: the composition root injects the split
        # application orchestrator while this runtime retains the frozen
        # golden-output implementation for existing callers.
        self.application_service = application_service
        self.bundle_freezer = BundleFreezeComponent(
            evidence_gateway=self.evidence_gateway,
            bundle_builder=self.bundle_builder,
            bundle_repository=self.bundle_repository,
            clock=clock,
            trusted_requests=self.trusted_evidence_requests,
        )
        self.specialist_runner = SpecialistRunnerComponent()
        self.snapshot_builder = SnapshotBuilderComponent(
            agent_enabled=self.agent_enabled,
            policy_engine=self.policy_engine,
            skill_snapshot=SnapshotBuilderComponent.skill_snapshot,
            specialist_artifact_type=SpecialistArtifact,
        )
        self.formal_calculator = FormalDecisionCalculatorComponent(
            claude_agent=self.claude_agent, trusted_fallback=self.trusted_fallback,
            policy_engine=self.policy_engine, agent_enabled=self.agent_enabled,
            investment_proposal_type=InvestmentProposal,
            policy_context_type=PolicyContext,
            specialist_role_type=AgentRole,
            specialist_status_type=SpecialistStatus,
            specialist_artifact_type=SpecialistArtifact,
            quality_fn=compute_decision_quality_v2,
            synthesis_fn=synthesize_evidence,
            operations={
                "metric": self._record_phase_metric,
                "specialists": self.specialist_runner.run,
                "snapshot": self.snapshot_builder.build,
                "save_decision": self.decision_service.save_decision,
                "snapshot_repository": self.snapshot_repository,
            },
        )
        self.analysis_service = AnalysisApplicationService(
            mode_resolver=self.resolve_runtime_mode,
            clock_now=self._clock_now,
            policy_engine=self.policy_engine,
            tool_results=self.tool_results,
            degraded_mode=RuntimeMode.DEGRADED_AGENT,
            task_graph_type=TaskGraph,
            task_type_factory=AgentTask,
            artifact_factory=AgentArtifact,
            success_status=TaskStatus.SUCCESS,
            supervisor_type=Supervisor,
            proposal_type=InvestmentProposal,
            policy_context_type=PolicyContext,
            profile_type=InvestorProfile,
            recommendation_type=Recommendation,
            suitability_fn=evaluate_suitability,
            conflict_resolver=resolve_conflicts_v2,
            runtime_segment_fn=build_runtime_segment,
        )

    @property
    def specialist_runner(self) -> SpecialistRunnerComponent:
        return self._specialist_runner

    @specialist_runner.setter
    def specialist_runner(self, value: Any) -> None:
        self._specialist_runner = value
        calculator = getattr(self, "formal_calculator", None)
        if calculator is not None:
            calculator.operations["specialists"] = value.run

    # ---------------------------------------------------------------- RuntimeMode
    def agent_enabled(self) -> bool:
        return bool(self.claude_agent.configured())

    def _record_phase_metric(self, name: str, value: float = 1.0, **labels: Any) -> None:
        if name.endswith("_latency_seconds"):
            self.metrics.observe(name, value, **labels)
        else:
            self.metrics.increment(name, amount=value, **labels)

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
    def freeze_bundle(
        self, *, task_type: str, objective: str, subjects: list[str] | None = None,
        context: dict[str, Any] | None = None, as_of: datetime | None = None,
        decision_id: str | None = None, snapshot_id: str | None = None,
    ) -> DecisionInputBundle:
        """Freeze external evidence through the bundle assembler component."""
        return self.bundle_freezer.freeze(
            task_type=task_type, objective=objective, subjects=subjects,
            context=context, as_of=as_of, decision_id=decision_id,
            snapshot_id=snapshot_id,
        )


    def decide_from_frozen_bundle(
        self, *, bundle: DecisionInputBundle | dict[str, Any], task_type: str,
        objective: str, subjects: list[str] | None = None, context: dict[str, Any] | None = None,
        decision_id: str | None = None, snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Run the complete formal pipeline using only a persisted bundle."""
        # A pre-v1 in-process caller may still pass the small assembler
        # mapping used by characterization tests. Keep that adapter explicit;
        # durable formal routes always deserialize DecisionInputBundle first.
        if not isinstance(bundle, DecisionInputBundle) and self.application_service is not None:
            return self.application_service.calculate_from_frozen(
                bundle=bundle, policy_version="formal-policy.v1",
            ) | {"decision_id": decision_id or str(uuid4()), "snapshot_id": snapshot_id or str(uuid4()),
                 "bundle_id": str(bundle.get("bundle_id") or bundle.get("bundle_hash")), "state": "FORMAL_CALCULATED"}
        item = bundle if isinstance(bundle, DecisionInputBundle) else DecisionInputBundle.model_validate(bundle)
        return self.formal_calculator.calculate(
            task_type=task_type, objective=objective, subjects=subjects or [],
            context=context, as_of=item.decision_time, decision_id=decision_id,
            snapshot_id=snapshot_id, frozen_bundle=item,
        )

    def decide(
        self,
        *,
        task_type: str,
        objective: str,
        subjects: list[str] | None = None,
        context: dict[str, Any] | None = None,
        as_of: datetime | None = None,
        decision_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Bundle-first formal decision entry point used by ``/api/v2``.

        The bundle is persisted before the model/fallback adapter is invoked.
        Any additional evidence is accepted only through a persisted patch.
        """
        # Compatibility façade: both legacy callers and the formal route use
        # the same freeze and frozen-calculation phases.
        bundle = self.freeze_bundle(
            task_type=task_type, objective=objective, subjects=subjects,
            context=context, as_of=as_of, decision_id=decision_id, snapshot_id=snapshot_id,
        )
        return self.decide_from_frozen_bundle(
            bundle=bundle, task_type=task_type, objective=objective,
            subjects=subjects, context=context, decision_id=decision_id,
            snapshot_id=snapshot_id,
        )

    def _run_formal_pipeline(self, **kwargs: Any) -> dict[str, Any]:
        """Compatibility wrapper for callers that used the old private hook."""
        return self.formal_calculator.calculate(**kwargs)

    formal_decision = decide

    @staticmethod
    def _safe_formal_context(context: dict[str, Any]) -> dict[str, Any]:
        """Persist only business selectors; public callers cannot persist facts."""
        allowed = {"skill", "account_id", "mode", "analysis_type", "subjects", "trace_context"}
        return {key: context[key] for key in allowed if key in context}

    # Formal evidence planning is owned by the extracted calculator component.
    _evidence_request_plan = staticmethod(FormalDecisionCalculatorComponent.evidence_request_plan)
    def _run_bundle_specialists(self, task_type: str, objective: str, context: dict[str, Any], decision_time: datetime, bundle: DecisionInputBundle) -> list[SpecialistArtifact]:
        return self.specialist_runner.run(
            task_type=task_type, objective=objective, context=context,
            decision_time=decision_time, bundle=bundle,
        )

    def _snapshot_v3(self, **kwargs: Any) -> DecisionSnapshotV3:
        return self.snapshot_builder.build(**kwargs)

    def _decision_time(self, value: datetime | None) -> datetime:
        result = value or self._clock_now()
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return result

    def _clock_now(self) -> datetime:
        value = self.clock() if callable(self.clock) else self.clock.now() if self.clock is not None and hasattr(self.clock, "now") else datetime.now(UTC)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value

    # ---------------------------------------------------------------- pipeline
    def _run_pipeline(self, **kwargs: Any) -> dict:
        """Compatibility façade for the non-authoritative analysis service."""
        return self.analysis_service.run_pipeline(**kwargs)

    def _run_narrative(self, **kwargs: Any) -> dict:
        """Compatibility façade for the non-authoritative analysis service."""
        return self.analysis_service.run_narrative(**kwargs)

    def _run_supervised(self, **kwargs: Any) -> dict:
        return self.analysis_service._run_supervised(**kwargs)

    def _govern(self, payload: dict) -> dict:
        return self.analysis_service._govern(payload)

    def _persist(self, **kwargs: Any) -> dict:
        return self.analysis_service._persist(**kwargs)

    @staticmethod
    def _content_lineage(payload: dict) -> tuple[dict, list[str]]:
        return AnalysisApplicationService._content_lineage(payload)

    def _record_tool_result(self, **kwargs: Any) -> dict:
        return self.analysis_service._record_tool_result(**kwargs)

    @staticmethod
    def _actionable_segment(governed: dict, persisted: dict, mode: RuntimeMode) -> dict:
        return AnalysisApplicationService._actionable_segment(governed, persisted, mode)





__all__ = ["DECISION_RUNTIME_VERSION", "LEGACY_SIGNAL_CONTRACT_VERSION", "DecisionRuntime"]

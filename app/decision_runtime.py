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

from agent.contracts import AgentArtifact, AgentRole, AgentTask, TaskStatus
from agent.supervisor import Supervisor
from agent.task_graph import TaskGraph
from app.claude_agent import ClaudeAgent
from app.fallback_orchestrator import LocalFallbackOrchestrator
from contracts.content import CONTENT_FACTOR_SIGNAL_VERSION
from engines.advisory.models import InvestorProfile, Recommendation
from engines.advisory.suitability import evaluate_suitability
from engines.decision.conflict_resolver import resolve_conflicts_v2
from engines.decision.decision_service import DecisionService
from engines.decision.runtime_mode import RuntimeMode, build_runtime_segment
from engines.policy.engine import PolicyEngine
from engines.policy.models import InvestmentProposal, PolicyContext
from storage.repositories.tool_result_repository import ToolResultRepository

DECISION_RUNTIME_VERSION = "decision-runtime.v1"
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
    ) -> None:
        self.claude_agent = claude_agent if claude_agent is not None else ClaudeAgent()
        self.fallback = fallback if fallback is not None else LocalFallbackOrchestrator()
        self.decision_service = decision_service or DecisionService()
        self.policy_engine = policy_engine or PolicyEngine()
        self.tool_results = tool_results or ToolResultRepository()

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

        return self._run_pipeline(
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

        return self._run_pipeline(
            task_type="analyze_theme",
            role=AgentRole.RESEARCH,
            objective=query,
            execute=_execute,
            query=query,
            subject=theme_name,
        )

    def daily_scan(self, scan_date: date | None = None, mode: str = "after_close") -> dict:
        query = f"请完成 {scan_date or date.today()} {mode} 的每日市场扫描，输出强主题、候选方向、仓位建议和风险提示。"

        def _execute() -> dict:
            if self.agent_enabled():
                result = self.claude_agent.run(
                    user_query=query,
                    context={"date": str(scan_date) if scan_date else None, "mode": mode},
                    force_skill="daily-market-decision",
                )
                return {
                    "date": str(scan_date or date.today()),
                    "mode": mode,
                    "orchestration": "claude-style-agent",
                    "selected_skill": result.selected_skill,
                    "selection_reason": result.selection_reason,
                    "tool_calls": result.tool_calls,
                    "trace": result.trace,
                    "report": result.report,
                }
            return self.fallback.daily_scan(scan_date=scan_date, mode=mode)

        return self._run_pipeline(
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
        mode, fallback_reason = self.resolve_runtime_mode()
        if mode == RuntimeMode.PRIMARY_AGENT:

            def _execute() -> dict:
                result = self.claude_agent.run(user_query=query, context=context, force_skill=skill, emit=emit)
                return {
                    "orchestration": "claude-style-agent",
                    "selected_skill": result.selected_skill,
                    "selection_reason": result.selection_reason,
                    "tool_calls": result.tool_calls,
                    "trace": result.trace,
                    "report": result.report,
                }
        else:
            # 显式降级：保留 legacy warning 语义，但 fallback 决策同样受治理并落库。
            payload = {
                "orchestration": "local-fallback",
                "warning": "主模型未配置，当前无法运行 Claude-style Agent。请配置 AGENT_MODEL_* 或 ANALYSIS_MODEL_* 为 DeepSeek/OpenAI-compatible 模型。",
                "trace": {
                    "selection_reason": "Primary agent model is unavailable.",
                    "steps": [
                        {
                            "type": "warning",
                            "title": "Model unavailable",
                            "content": "AGENT_MODEL_* or ANALYSIS_MODEL_* is not configured, so the chat agent could not start.",
                        }
                    ],
                },
            }
            governed = self._govern(payload)
            persisted = self._persist(
                objective=query,
                payload=payload,
                governed=governed,
                mode=mode,
                fallback_reason=fallback_reason,
                task_type="run_agent",
                subject=None,
                agent_run_id=None,
                decision_quality="DEGRADED",
            )
            payload.update(self._actionable_segment(governed, persisted, mode))
            if emit:
                emit("warning", {"message": payload["warning"]})
                emit("trace", {"step": payload["trace"]["steps"][0]})
                emit("done", payload)
            return payload

        return self._run_pipeline(
            task_type="run_agent",
            role=AgentRole.RESEARCH,
            objective=query,
            execute=_execute,
            query=query,
            subject=None,
            emit=emit,
        )

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
        as_of_dt = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC) if isinstance(as_of, date) else datetime.now(UTC)
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

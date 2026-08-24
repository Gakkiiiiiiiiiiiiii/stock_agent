from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.model_providers import AgentModelClient, AgentModelSettings
from app.model_gateway import TraceContext
from app.skill_loader import SkillDefinition, format_skill_catalog, load_skills
from app.tool_registry import ClaudeToolRegistry
from agent.executor import SkillExecutor
from engines.market.trading_clock import TradingClock, get_default_clock
from contracts.proposal import DecisionHorizon, InvestmentProposalV2, ModelIdentity, NarrativeReport, ThesisPoint
from pydantic import BaseModel, ConfigDict, Field


@dataclass
class ClaudeAgentResponse:
    selected_skill: str
    selection_reason: str
    report: str
    tool_calls: list[dict[str, Any]]
    trace: dict[str, Any]
    raw_text: str | None = None
    decision_id: str | None = None


@dataclass
class SkillSelectionDecision:
    skill: SkillDefinition
    reason: str


class FormalModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal: dict
    report: str | dict
    missing_evidence_requests: list[dict] = Field(default_factory=list)
    missing_evidence_reason: str | None = None


def _skill_identity_payload(skill: SkillDefinition) -> dict[str, Any]:
    return {
        "slug": skill.slug,
        "version": skill.version,
        "contract_hash": skill.skill_contract_hash,
        "markdown_hash": skill.skill_markdown_hash,
    }


def _formal_horizon(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"period": value, "start": None, "end": None}
    if isinstance(value, dict):
        return {"period": value.get("period", "UNSPECIFIED"), "start": value.get("start"), "end": value.get("end")}
    return {"period": "UNSPECIFIED", "start": None, "end": None}


class _SkillIdentityToolProxy:
    """Wraps the tool registry so save_investment_decision persists which exact
    skill contract (slug + version + hashes) produced the decision, even when the
    model does not pass those fields itself. Model-supplied values win."""

    def __init__(self, registry: ClaudeToolRegistry, skill: SkillDefinition) -> None:
        self._registry = registry
        self._skill = skill

    def openai_tools(self) -> list[dict[str, Any]]:
        return self._registry.openai_tools()

    def describe_tool(self, name: str) -> str:
        return self._registry.describe_tool(name)

    def execute(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "save_investment_decision":
            payload = dict(payload or {})
            payload.setdefault("skill_slug", self._skill.slug)
            payload.setdefault("skill_version", self._skill.version)
            payload.setdefault("skill_contract_hash", self._skill.skill_contract_hash)
            payload.setdefault("skill_markdown_hash", self._skill.skill_markdown_hash)
        return self._registry.execute(name, payload)


class ClaudeAgent:
    """
    Keep the ClaudeAgent name because the project still follows the Claude-style
    agent architecture: skills + controlled tools + orchestration loop.
    The underlying model, however, is an OpenAI-compatible provider such as DeepSeek.
    """

    def __init__(
        self,
        client: AgentModelClient | Any | None = None,
        tools: ClaudeToolRegistry | None = None,
        skills: list[SkillDefinition] | None = None,
        model: str | None = None,
        max_tool_rounds: int = 8,
        clock: TradingClock | None = None,
    ) -> None:
        if client is not None:
            self.client = client
        elif model:
            base = AgentModelSettings.from_env()
            self.client = AgentModelClient(
                settings=AgentModelSettings(
                    provider=base.provider,
                    model=model,
                    base_url=base.base_url,
                    api_key=base.api_key,
                    capabilities=base.capabilities,
                )
            )
        else:
            self.client = AgentModelClient()
        self.tool_registry = tools or ClaudeToolRegistry()
        self.skills = skills or load_skills()
        self.max_tool_rounds = max_tool_rounds
        self.clock = clock if clock is not None else get_default_clock()

    def configured(self) -> bool:
        return bool(getattr(self.client, "available", lambda: False)())

    def run_formal(self, *, user_query: str, context: dict[str, Any] | None = None, force_skill: str | None = None) -> dict[str, Any]:
        """Bundle-only model call used by the formal v2 runtime.

        The method intentionally accepts only a validated DecisionInputBundle
        and specialist artifacts. It never exposes a tool registry or invokes
        the legacy ``run`` loop.
        """
        from contracts.decision_input import DecisionInputBundle
        from agent.contracts import SpecialistArtifact

        context = dict(context or {})
        raw_bundle = context.get("decision_input_bundle")
        if not context.get("bundle_only") or context.get("allow_external_evidence_calls"):
            raise ValueError("BUNDLE_ONLY_CONTEXT_REQUIRED")
        bundle = DecisionInputBundle.model_validate(raw_bundle)
        artifacts = [SpecialistArtifact.model_validate(item) for item in (context.get("specialist_artifacts") or [])]
        if not artifacts:
            # D3 may pass artifacts separately while preparing formal_context;
            # absence is still explicit and is represented in the prompt.
            artifacts = []
        trace_values = dict(context.get("trace_context") or {})
        trace = TraceContext(
            trace_id=str(trace_values.get("trace_id") or TraceContext().trace_id),
            decision_id=trace_values.get("decision_id"), bundle_id=bundle.bundle_id,
            agent_run_id=trace_values.get("agent_run_id"), proposal_id=trace_values.get("proposal_id"),
            snapshot_id=trace_values.get("snapshot_id"),
        )
        safe_input = {
            "bundle": bundle.model_dump(mode="json"),
            "specialist_artifacts": [item.model_dump(mode="json") for item in artifacts],
            "skill": force_skill or context.get("skill"),
            # The objective is read from the validated bundle; raw user_query
            # is intentionally not added as an independent model fact.
            "objective": bundle.objective,
        }
        system = (
            "You are the formal decision model. Use only the supplied immutable bundle and specialist artifacts. "
            "Do not call tools, request external data, or invent evidence. Return one JSON object with proposal and report."
        )
        prompt = json.dumps(safe_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        raw = self._formal_model_call(prompt, system, trace=trace)
        output = self._parse_formal_output(raw)
        identity = self._model_identity()
        proposal_payload = dict(output.proposal)
        proposal_payload["proposal_id"] = str(proposal_payload.get("proposal_id") or f"proposal-{bundle.bundle_id}")
        proposal_payload.setdefault("schema_version", "investment-proposal.v2")
        proposal_payload["generated_by"] = identity.model_dump(mode="json")
        proposal_payload.setdefault("target_weight", None)
        proposal_payload.setdefault("weight_delta", None)
        for field_name in ("catalysts", "entry_conditions", "invalidation_conditions", "expected_risks", "evidence_refs", "specialist_artifact_refs", "unknowns"):
            proposal_payload.setdefault(field_name, [])
        proposal_payload["evidence_refs"] = list(proposal_payload.get("evidence_refs") or [])
        allowed_evidence = {item.evidence_id for item in bundle.evidence}
        if any(ref not in allowed_evidence for ref in proposal_payload["evidence_refs"]):
            raise ValueError("MODEL_EVIDENCE_REF_OUTSIDE_BUNDLE")
        proposal_payload["specialist_artifact_refs"] = list(proposal_payload.get("specialist_artifact_refs") or [item.artifact_id for item in artifacts])
        allowed_artifacts = {item.artifact_id for item in artifacts}
        if any(ref not in allowed_artifacts for ref in proposal_payload["specialist_artifact_refs"]):
            raise ValueError("MODEL_SPECIALIST_REF_OUTSIDE_CONTEXT")
        proposal_payload["horizon"] = _formal_horizon(proposal_payload.get("horizon"))
        proposal_payload["thesis"] = [item if isinstance(item, dict) else {"statement": str(item), "evidence_refs": []} for item in (proposal_payload.get("thesis") or [])]
        proposal = InvestmentProposalV2.build(**proposal_payload)
        report = output.report if isinstance(output.report, dict) else {"summary": output.report}
        narrative = NarrativeReport.model_validate(report)
        result = {
            "proposal": proposal.model_dump(mode="json"), "report": narrative.model_dump(mode="json"),
            "selected_skill": force_skill or context.get("skill"), "model": identity.model,
            "model_version": identity.model_version, "provider": identity.provider,
            "prompt_version": "formal-decision.v2", "tool_calls": 0,
            "missing_evidence_requests": output.missing_evidence_requests,
            "missing_evidence_reason": output.missing_evidence_reason,
        }
        if isinstance(raw, dict):
            for key in ("usage", "cost", "trace_id"):
                if key in raw:
                    result[key] = raw[key]
        elif hasattr(raw, "as_dict"):
            result.update({key: raw.as_dict().get(key) for key in ("usage", "cost", "trace_id") if raw.as_dict().get(key) is not None})
        return result

    def _formal_model_call(self, prompt: str, system: str, *, trace: TraceContext | None = None) -> Any:
        complete = getattr(self.client, "complete", None)
        if callable(complete):
            try:
                return complete(prompt=prompt, system=system, max_tokens=4096, temperature=0.0, output_model=FormalModelOutput, trace=trace)
            except TypeError as exc:
                if "trace" not in str(exc):
                    raise
                return complete(prompt=prompt, system=system, max_tokens=4096, temperature=0.0, output_model=FormalModelOutput)
        create = getattr(self.client, "create_chat_completion", None)
        if callable(create):
            try:
                return create(system=system, messages=[{"role": "user", "content": prompt}], max_tokens=4096, temperature=0.0, trace=trace)
            except TypeError as exc:
                if "trace" not in str(exc):
                    raise
                return create(system=system, messages=[{"role": "user", "content": prompt}], max_tokens=4096, temperature=0.0)
        raise ValueError("FORMAL_MODEL_CLIENT_REQUIRED")

    @staticmethod
    def _parse_formal_output(raw: Any) -> FormalModelOutput:
        if hasattr(raw, "structured_output") and raw.structured_output is not None:
            return FormalModelOutput.model_validate(raw.structured_output)
        if isinstance(raw, dict) and isinstance(raw.get("structured_output"), dict):
            return FormalModelOutput.model_validate(raw["structured_output"])
        if isinstance(raw, dict) and isinstance(raw.get("proposal"), dict):
            return FormalModelOutput.model_validate(raw)
        content = raw.get("content", "") if isinstance(raw, dict) else getattr(raw, "content", "")
        if isinstance(content, str):
            try:
                return FormalModelOutput.model_validate(json.loads(content))
            except Exception as exc:  # noqa: BLE001 - normalize provider output
                raise ValueError("FORMAL_STRUCTURED_OUTPUT_INVALID") from exc
        raise ValueError("FORMAL_STRUCTURED_OUTPUT_REQUIRED")

    def _model_identity(self) -> ModelIdentity:
        settings = getattr(self.client, "settings", None)
        return ModelIdentity(
            provider=str(getattr(settings, "provider", getattr(self.client, "provider", "unknown"))),
            model=str(getattr(settings, "model", getattr(self.client, "model", "unknown"))),
            model_version=getattr(settings, "model_version", getattr(self.client, "model_version", None)),
        )

    def run(
        self,
        user_query: str,
        context: dict[str, Any] | None = None,
        force_skill: str | None = None,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> ClaudeAgentResponse:
        if not self.configured():
            raise RuntimeError("Primary agent model is not configured")
        self._emit(emit, "status", {"message": "Selecting skill..."})
        decision = self._choose_skill(user_query, context, force_skill=force_skill)
        from agent.context_builder import ContextBuilder

        context = ContextBuilder().build(user_query, context)
        multi_agent_result = self._run_multi_agent_preflight(decision.skill, user_query, context)
        if multi_agent_result is not None:
            context = {**context, "multi_agent": multi_agent_result}
        self._emit(
            emit,
            "selection",
            {
                "orchestration": "claude-style-agent",
                "selected_skill": decision.skill.slug,
                "selection_reason": decision.reason,
            },
        )
        self._emit(
            emit,
            "trace",
            {
                "step": {
                    "type": "skill_selection",
                    "title": "Skill selected",
                    "content": decision.reason,
                    "data": {"skill": decision.skill.slug},
                }
            },
        )
        self._emit(emit, "status", {"message": f"Running skill: {decision.skill.slug}"})
        report, tool_calls, trace_steps = self._run_skill(decision.skill, user_query, context, emit=emit)
        decision_id = self._ensure_formal_decision(decision.skill, user_query, report, tool_calls, multi_agent_result)
        trace = {
            "selection_reason": decision.reason,
            "skill": _skill_identity_payload(decision.skill),
            "steps": [
                {
                    "type": "skill_selection",
                    "title": "Skill selected",
                    "content": decision.reason,
                    "data": {"skill": decision.skill.slug},
                },
                *([{"type": "multi_agent", "title": "Specialist DAG", "content": "Daily-decision specialists completed.", "data": multi_agent_result}] if multi_agent_result is not None else []),
                *trace_steps,
            ],
        }
        return ClaudeAgentResponse(
            selected_skill=decision.skill.slug,
            selection_reason=decision.reason,
            report=report,
            tool_calls=tool_calls,
            trace=trace,
            raw_text=report,
            decision_id=decision_id,
        )

    def _run_multi_agent_preflight(self, skill: SkillDefinition, query: str, context: dict[str, Any]) -> dict[str, Any] | None:
        """Run the bounded specialist DAG for configured skills, then let the
        normal SkillExecutor remain the single final-report path."""
        from agent.supervisor import Supervisor, load_multi_agent_config
        from agent.plans.daily_market_decision import build_daily_market_decision_graph
        from agent.specialists import FactorSpecialist, MarketSpecialist, PortfolioSpecialist, ResearchSpecialist, RiskSpecialist, TechnicalSpecialist
        from storage.bootstrap import create_all
        from storage.repositories.p2_repository import P2Repository
        from agent.contracts import AgentRole

        config = load_multi_agent_config()
        if not config.get("enabled") or skill.slug not in set(config.get("enabled_skills") or []):
            return None
        graph = build_daily_market_decision_graph(query, token_budget=min(8_000, int(config.get("max_total_llm_tokens", 60_000))), tool_budget=10)
        specialists = {
            AgentRole.MARKET: MarketSpecialist(self.tool_registry, context), AgentRole.RESEARCH: ResearchSpecialist(self.tool_registry, context),
            AgentRole.TECHNICAL: TechnicalSpecialist(self.tool_registry, context),
            AgentRole.FACTOR: FactorSpecialist(self.tool_registry, context),
            AgentRole.PORTFOLIO: PortfolioSpecialist(self.tool_registry, context), AgentRole.RISK: RiskSpecialist(self.tool_registry, context),
        }
        create_all()
        return Supervisor(specialists, config, repository=P2Repository()).run(graph)

    @staticmethod
    def _ensure_formal_decision(skill: SkillDefinition, query: str, report: str, tool_calls: list[dict[str, Any]], multi_agent_result: dict[str, Any] | None = None) -> str | None:
        if skill.slug != "daily-market-decision":
            return None
        for call in tool_calls:
            if call.get("name") == "save_investment_decision":
                output = call.get("output") or {}
                if output.get("decision_id"):
                    decision_id = str(output["decision_id"])
                    ClaudeAgent._attach_agent_run(decision_id, multi_agent_result)
                    return decision_id
        from engines.decision.decision_service import DecisionService

        regime_call = next((item.get("output") or {} for item in reversed(tool_calls) if item.get("name") == "get_market_regime"), {})
        saved = DecisionService().save_decision(
            query=query,
            skill_slug=skill.slug,
            skill_version=skill.version,
            skill_contract_hash=skill.skill_contract_hash,
            skill_markdown_hash=skill.skill_markdown_hash,
            market_regime=(regime_call.get("regime") or {}).get("primary_regime"),
            market_features=regime_call.get("features") or {},
            thesis={"report": report},
            tool_trace=tool_calls,
        )
        decision_id = str(saved["decision_id"])
        ClaudeAgent._attach_agent_run(decision_id, multi_agent_result)
        return decision_id

    @staticmethod
    def _attach_agent_run(decision_id: str, multi_agent_result: dict[str, Any] | None) -> None:
        run_id = (multi_agent_result or {}).get("agent_run_id")
        if run_id:
            from storage.repositories.research_repository import DecisionRepository
            DecisionRepository().attach_agent_run(decision_id, str(run_id), supervisor_version="v1")

    def _choose_skill(
        self,
        user_query: str,
        context: dict[str, Any] | None = None,
        force_skill: str | None = None,
    ) -> SkillSelectionDecision:
        if force_skill:
            skill = self._find_skill(force_skill)
            if skill is not None:
                return SkillSelectionDecision(skill=skill, reason=f"Skill forced by caller: {force_skill}")
            raise ValueError(f"unknown skill: {force_skill}")
        preselected = self._preselect_skill(user_query)
        if preselected is not None:
            return preselected
        skill_catalog = format_skill_catalog(self.skills)
        response = self.client.create_chat_completion(
            system=(
                "You are the orchestration brain for a financial research agent. "
                "Pick exactly one skill that best matches the task. "
                "If the user asks about recent/current investable sectors, themes, market directions, or what is worth watching now, "
                "prefer daily-market-decision over static theme research. "
                f"The current trading session is {self.clock.current_trading_session('CN_A').isoformat()}. "
                "Return only a JSON object like "
                '{"skill_slug":"...", "reason":"..."} '
                "with no markdown fences and no extra text."
            ),
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"User query:\n{user_query}\n\n"
                        f"Context:\n{json.dumps(context or {}, ensure_ascii=False)}\n\n"
                        f"Available skills:\n{skill_catalog}"
                    ),
                }
            ],
        )
        message = ((response.get("choices") or [{}])[0]).get("message", {})
        content = (message.get("content") or "").strip()
        try:
            payload = self._parse_json_object(content)
            selected = payload.get("skill_slug")
            if selected:
                for skill in self.skills:
                    if skill.slug == selected or skill.name == selected:
                        reason = str(payload.get("reason") or f"Model selected skill {selected} for this task.")
                        return SkillSelectionDecision(skill=skill, reason=reason)
        except Exception:
            pass
        fallback = self._fallback_choose_skill(user_query)
        if fallback is not None:
            return SkillSelectionDecision(skill=fallback, reason="Fallback keyword routing selected this skill.")
        raise RuntimeError("Primary agent model did not select a skill")

    def _run_skill(
        self,
        skill: SkillDefinition,
        user_query: str,
        context: dict[str, Any] | None = None,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        system = (
            "You are the primary model running a Claude-style financial analysis agent framework. "
            "You must personally do skill execution planning, tool invocation, and final report writing. "
            "Use the provided tools for all deterministic computation and data retrieval. "
            "If the ask_research_model tool is available, you may use it as a subordinate helper, "
            "but you remain responsible for the final judgment. "
            f"Never fabricate missing market data. If data is insufficient, say so clearly. Current trading session is {self.clock.current_trading_session('CN_A').isoformat()}.\n\n"
            f"Selected skill: {skill.slug}\n\n"
            f"Skill instructions:\n{skill.instructions}\n\n"
            "The selected skill has an executable contract. You must complete it before giving a final answer. "
            "Before any tool call, you may write a short user-visible execution note. "
            "Keep it brief and factual. Do not reveal hidden chain-of-thought."
        )
        report, tool_calls, trace_steps, _state = SkillExecutor(
            self.client, _SkillIdentityToolProxy(self.tool_registry, skill), self.max_tool_rounds
        ).run(skill=skill, user_query=user_query, context=context, system=system, emit=emit, query_flags=(context or {}).get("query_flags"))
        return report, tool_calls, trace_steps

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
        return json.loads(text)

    def _fallback_choose_skill(self, user_query: str) -> SkillDefinition | None:
        query = user_query.lower()
        rules = [
            ("factor-mining", ["挖因子", "因子挖掘", "因子", "alpha"]),
            ("portfolio-construction", ["组合", "仓位", "持仓", "配仓", "防守", "标的"]),
            ("portfolio-risk-review", ["风控", "风险", "暴露", "集中度"]),
            ("daily-market-decision", ["最近", "近期", "当前", "今天", "板块", "赛道", "方向", "机会", "主线", "值得关注", "值得投资"]),
            ("industry-logic-research", ["主题", "产业链", "催化", "证伪", "黄金", "逻辑"]),
            ("market-regime-strategy-router", ["市场状态", "风格", "轮动", "退潮", "regime"]),
            ("a-share-technical-analysis", ["技术", "k线", "b1", "b2", "b3", "macd", "rps"]),
            ("post-trade-review", ["复盘", "交易后", "卖出", "买入原因"]),
            ("decision-conflict-resolver", ["冲突", "矛盾", "取舍", "分歧"]),
            ("daily-market-decision", ["每日", "日内", "扫描", "今日计划"]),
        ]
        for slug, keywords in rules:
            if any(keyword in query for keyword in keywords):
                skill = self._find_skill(slug)
                if skill is not None:
                    return skill
        return self.skills[0] if self.skills else None

    def _preselect_skill(self, user_query: str) -> SkillSelectionDecision | None:
        if self._is_recent_market_opportunity_query(user_query):
            skill = self._find_skill("daily-market-decision")
            if skill is not None:
                return SkillSelectionDecision(
                    skill=skill,
                    reason="Query asks for recent/current investable sectors or directions, so it should use daily-market-decision with fresh market context and latest video insights.",
                )
        return None

    def _find_skill(self, slug_or_name: str) -> SkillDefinition | None:
        for skill in self.skills:
            if skill.slug == slug_or_name or skill.name == slug_or_name:
                return skill
        return None

    @staticmethod
    def _is_recent_market_opportunity_query(user_query: str) -> bool:
        query = (user_query or "").strip().lower()
        if not query:
            return False
        recency_keywords = ("最近", "近期", "当前", "今天", "这两天", "这几天", "最新", "本周", "眼下")
        opportunity_keywords = ("板块", "赛道", "方向", "机会", "主线", "值得关注", "值得投资", "可交易", "怎么看")
        return any(keyword in query for keyword in recency_keywords) and any(keyword in query for keyword in opportunity_keywords)

    @staticmethod
    def _emit(emit: Callable[[str, dict[str, Any]], None] | None, event: str, payload: dict[str, Any]) -> None:
        if emit is None:
            return
        emit(event, payload)

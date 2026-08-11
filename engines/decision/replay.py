"""决策回放（Decision Replay，设计文档 §27）。

用决策落库时的确定性输入（candidates / market_features / market_regime /
benchmark_route 等）重放决策链中的**确定性**环节：

  候选 → OpportunityRankingService.rank（资格过滤 + 打分 + 排序）
       → run_portfolio_pipeline（v2 组合构建，使用决策记录的 regime）
       → BenchmarkRouter.route（由落库属性重建基准路由）

两种模式：
  - "original"：版本锚定到决策记录的版本（replay_versions = 记录版本）。
  - "current"：同一批落库输入，版本取当前代码/配置版本。

已知限制（重要）：
  1. **无法做真正的历史代码回放**：代码没有版本化存储，original 模式仍然用
     当前代码执行；当记录版本与当前版本不一致时置 version_mismatch=True
     并在 version_mismatch_details 中列出差异（replay_uses_current_code=True
     始终为真，提醒调用方这一点）。
  2. LLM 环节（skill 选择、thesis 生成等）不在回放范围内，只覆盖确定性产物。
  3. 决策保存路由输入与所有确定性模块版本；旧记录缺失这些字段时才按候选
     退化重建，并在返回中明确标记。

"可比部分"（match 的判定范围，其余字段不参与比对）：
  - candidate_order：落库候选的符号顺序（候选均带 rank 字段时按 rank 升序，
    否则按列表顺序；仅保留重放后仍 eligible 的符号）vs 重放 ranked 顺序；
  - portfolio_actions：落库 portfolio_advice.actions 与重放 actions 按 symbol
    比对 (action, target_weight)；
  - benchmark_primary：落库 benchmark_route.primary_benchmark vs 重放主基准。
落库侧对应产物缺失时跳过该项比对（视为无历史基线，不算 diff）。
行情数据只读取持久化快照（MarketFeatureRepository），无快照时回退到
decision.market_features JSON；无网络、无 LLM，整体确定性。
"""
from __future__ import annotations

import json
from typing import Any

from engines.decision.benchmark_router import BenchmarkRouter
from engines.opportunity.service import OpportunityRankingService
from engines.portfolio.pipeline import run_portfolio_pipeline
from engines.versioning import get_version
from storage.bootstrap import create_all
from storage.repositories.market_feature_repository import MarketFeatureRepository
from storage.repositories.research_repository import DecisionRepository
from storage.repositories.p2_repository import P2Repository

REPLAY_MODES = ("original", "current", "multi_agent")

#: 参与版本比对的算法版本键（skill_* 为内容身份，不参与 mismatch 判定）。
_COMPARABLE_VERSION_KEYS = (
    "market_feature_version",
    "opportunity_ranking_version",
    "portfolio_rule_version",
    "benchmark_router_version",
)


class DecisionReplayService:
    """从落库数据重建确定性决策链并与落库产物比对（无 LLM、无网络）。"""

    def __init__(
        self,
        repository: DecisionRepository | None = None,
        market_features: MarketFeatureRepository | None = None,
    ) -> None:
        self.repository = repository or DecisionRepository()
        self.market_features = market_features or MarketFeatureRepository()

    def replay(self, decision_id: str, mode: str = "original") -> dict:
        if mode not in REPLAY_MODES:
            return {"error": "INVALID_REPLAY_MODE", "decision_id": decision_id, "mode": mode, "supported_modes": list(REPLAY_MODES)}
        create_all()
        decision = self.repository.get(decision_id)
        if decision is None:
            return {"error": "DECISION_NOT_FOUND", "decision_id": decision_id}

        market_features, market_feature_source = self._resolve_market_features(decision, mode)
        multi_agent = self._multi_agent_provenance(decision) if mode == "multi_agent" else None
        if mode == "multi_agent" and multi_agent and multi_agent.get("available"):
            replay_output, current_versions = self._run_multi_agent_chain(decision, multi_agent)
        else:
            replay_output, current_versions = self._run_chain(decision)

        recorded_versions = self._recorded_versions(decision)
        mismatch_details = {
            key: {"recorded": recorded_versions.get(key), "current": current_versions.get(key)}
            for key in _COMPARABLE_VERSION_KEYS
            if recorded_versions.get(key) is not None and str(recorded_versions.get(key)) != str(current_versions.get(key))
        }
        replay_versions = (
            {key: recorded_versions.get(key) for key in _COMPARABLE_VERSION_KEYS}
            if mode == "original"
            else dict(current_versions)
        )

        original_output = {
            "candidates": list(decision.candidates or []),
            "portfolio_advice": dict(decision.portfolio_advice or {}),
            "benchmark_route": dict(decision.benchmark_route or {}),
        }
        diffs = self._diff(original_output, replay_output)
        if mode == "multi_agent" and multi_agent and multi_agent.get("available"):
            recorded_regime = decision.market_regime
            replayed_regime = replay_output.get("multi_agent_context", {}).get("market_regime")
            if recorded_regime is not None and replayed_regime is not None and recorded_regime != replayed_regime:
                diffs.append({"field": "market_regime", "stored": recorded_regime, "replayed": replayed_regime})
            stored_risk_veto = self._stored_risk_veto(decision, multi_agent)
            replayed_risk_veto = replay_output.get("risk_veto")
            if stored_risk_veto is not None and replayed_risk_veto is not None and stored_risk_veto != replayed_risk_veto:
                diffs.append({"field": "risk_veto", "stored": stored_risk_veto, "replayed": replayed_risk_veto})

        return self._json_safe(
            {
                "decision_id": decision_id,
                "mode": mode,
                "input_versions": recorded_versions,
                "replay_versions": replay_versions,
                "version_mismatch": bool(mismatch_details),
                "version_mismatch_details": mismatch_details,
                "replay_uses_current_code": True,
                "market_feature_source": market_feature_source,
                "market_features": market_features,
                "original_output": original_output,
                "replay_output": replay_output,
                "multi_agent": multi_agent,
                "match": not diffs,
                "diffs": diffs,
            }
        )

    @staticmethod
    def _multi_agent_provenance(decision: Any) -> dict:
        if not decision.agent_run_id:
            return {"available": False, "reason": "AGENT_RUN_NOT_ATTACHED"}
        repository = P2Repository()
        run = repository.get_agent_run(decision.agent_run_id)
        if run is None:
            return {"available": False, "reason": "AGENT_RUN_NOT_FOUND", "agent_run_id": decision.agent_run_id}
        subtasks = repository.list_subtasks(run.id)
        conflicts = repository.list_conflicts(run.id)
        return {
            "available": True,
            "agent_run_id": run.id,
            "status": run.status,
            "usage": run.usage,
            "artifacts": [{"task_id": item.id, "agent": item.agent, "status": item.status, "conclusion": item.conclusion} for item in subtasks],
            "conflicts": [{"dimension": item.dimension, "resolved_value": item.resolved_value, "resolved_by": item.resolved_by} for item in conflicts],
        }

    # ---- 确定性链重建 -------------------------------------------------------

    def _run_chain(self, decision: Any) -> tuple[dict, dict]:
        """用落库输入重跑 rank → portfolio v2 → benchmark route，返回 (输出, 当前版本)。"""
        raw_candidates = [dict(item) for item in (decision.candidates or []) if isinstance(item, dict)]
        as_of = (decision.decision_as_of or decision.created_at).isoformat()

        ranking = OpportunityRankingService().rank(raw_candidates, {"as_of": as_of})
        score_map = {item["symbol"]: item["opportunity_score"] for item in ranking["ranked"]}
        pipeline_candidates = []
        for item in raw_candidates:
            candidate = dict(item)
            if candidate.get("symbol") in score_map:
                candidate["opportunity_score"] = score_map[candidate["symbol"]]
            pipeline_candidates.append(candidate)
        portfolio = run_portfolio_pipeline(
            pipeline_candidates,
            [],
            context={"regime": decision.market_regime, "as_of": as_of},
        )
        route = BenchmarkRouter().route(self._route_attributes(decision, raw_candidates))

        current_versions = {
            "market_feature_version": get_version("market_feature_version"),
            "opportunity_ranking_version": ranking["meta"].get("calculation_version"),
            "portfolio_rule_version": (portfolio.get("summary") or {}).get("rules_version"),
            "benchmark_router_version": route.get("router_version"),
        }
        return {"ranked": ranking, "portfolio": portfolio, "benchmark_route": route}, current_versions

    def _run_multi_agent_chain(self, decision: Any, provenance: dict) -> tuple[dict, dict]:
        """Rebuild only deterministic stages from persisted specialist output."""
        artifacts = {item["agent"]: dict(item.get("conclusion") or {}) for item in provenance.get("artifacts") or []}
        market = artifacts.get("MarketAgent", {})
        regime_payload = market.get("get_market_regime") or market.get("market_regime") or {}
        if isinstance(regime_payload, dict) and isinstance(regime_payload.get("regime"), dict):
            regime = regime_payload["regime"].get("primary_regime")
        elif isinstance(regime_payload, dict):
            regime = regime_payload.get("primary_regime")
        else:
            regime = regime_payload
        regime = regime or decision.market_regime
        technical = artifacts.get("TechnicalAgent", {}).get("technical") or {}
        technical_candidates = technical.get("candidates") or technical.get("ranked") if isinstance(technical, dict) else None
        candidates = [dict(item) for item in (technical_candidates or decision.candidates or []) if isinstance(item, dict)]
        # Persisted conflict resolution is a deterministic input, not prose.
        resolved = {item["dimension"]: (item.get("resolved_value") or {}).get("value") for item in provenance.get("conflicts") or []}
        ranking = OpportunityRankingService().rank(candidates, {"as_of": (decision.decision_as_of or decision.created_at).isoformat(), "market_regime": regime, "resolved_conflicts": resolved})
        score_map = {item["symbol"]: item["opportunity_score"] for item in ranking["ranked"]}
        pipeline_candidates = [{**item, **({"opportunity_score": score_map[item["symbol"]]} if item.get("symbol") in score_map else {})} for item in candidates]
        portfolio = run_portfolio_pipeline(pipeline_candidates, [], context={"regime": regime, "resolved_conflicts": resolved})
        route = BenchmarkRouter().route(self._route_attributes(decision, candidates))
        risk = artifacts.get("RiskAgent", {})
        return {
            "ranked": ranking,
            "portfolio": portfolio,
            "benchmark_route": route,
            "risk_veto": self._risk_veto(risk),
            "multi_agent_context": {"market_regime": regime, "resolved_conflicts": resolved, "artifact_agents": sorted(artifacts)},
        }, {
            "market_feature_version": get_version("market_feature_version"),
            "opportunity_ranking_version": ranking["meta"].get("calculation_version"),
            "portfolio_rule_version": (portfolio.get("summary") or {}).get("rules_version"),
            "benchmark_router_version": route.get("router_version"),
        }

    @classmethod
    def _stored_risk_veto(cls, decision: Any, provenance: dict) -> bool | None:
        """Use the recorded decision baseline first, then the original Risk artifact."""
        for source in (decision.thesis or {}, decision.portfolio_advice or {}):
            if isinstance(source, dict) and "risk_veto" in source:
                return bool(source["risk_veto"])
        artifacts = {item["agent"]: item.get("conclusion") or {} for item in provenance.get("artifacts") or []}
        risk = artifacts.get("RiskAgent")
        return cls._risk_veto(risk) if isinstance(risk, dict) else None

    @staticmethod
    def _risk_veto(payload: dict) -> bool:
        if "veto" in payload:
            return bool(payload["veto"])
        for key in ("risk", "evaluate_portfolio_risk", "portfolio_risk"):
            nested = payload.get(key)
            if isinstance(nested, dict) and "veto" in nested:
                return bool(nested["veto"])
        return False

    @staticmethod
    def _route_attributes(decision: Any, candidates: list[dict]) -> dict:
        """Use the persisted router input; fall back only for old decisions."""
        if decision.benchmark_route_input:
            return dict(decision.benchmark_route_input)
        sectors = {str(item["sector"]) for item in candidates if item.get("sector")}
        return {
            "symbols": [str(item["symbol"]) for item in candidates if item.get("symbol")],
            "themes": list(decision.themes or []),
            "decision_type": decision.decision_type,
            "style": decision.style,
            "market": decision.market,
            "sector": sectors.pop() if len(sectors) == 1 else None,
        }

    def _resolve_market_features(self, decision: Any, mode: str) -> tuple[dict, str]:
        """行情特征只取持久化快照；无快照时回退 decision.market_features JSON。"""
        anchor = decision.data_as_of or decision.decision_as_of or decision.created_at
        feature_version = decision.market_feature_version if mode == "original" else (get_version("market_feature_version") or decision.market_feature_version)
        if anchor is not None:
            snapshot = self.market_features.get_market_snapshot("CN_A", anchor.date(), feature_version)
            if snapshot is not None:
                return dict(snapshot.features_json or {}), "snapshot"
        if decision.market_features:
            return dict(decision.market_features), "decision"
        return {}, "none"

    @staticmethod
    def _recorded_versions(decision: Any) -> dict:
        portfolio_advice = decision.portfolio_advice or {}
        benchmark_route = decision.benchmark_route or {}
        return {
            "market_feature_version": decision.market_feature_version,
            "opportunity_ranking_version": decision.opportunity_ranking_version,
            "portfolio_rule_version": decision.portfolio_rule_version or (portfolio_advice.get("summary") or {}).get("rules_version"),
            "benchmark_router_version": decision.benchmark_router_version or benchmark_route.get("router_version"),
            "skill_version": decision.skill_version,
            "skill_contract_hash": decision.skill_contract_hash,
        }

    # ---- 可比部分比对 --------------------------------------------------------

    @classmethod
    def _diff(cls, original: dict, replay: dict) -> list[dict]:
        diffs: list[dict] = []

        stored_order = cls._stored_candidate_order(original["candidates"])
        replay_order = [item["symbol"] for item in replay["ranked"]["ranked"]]
        if stored_order:
            replayed_symbols = set(replay_order)
            comparable_stored = [symbol for symbol in stored_order if symbol in replayed_symbols]
            if comparable_stored != replay_order:
                diffs.append({"field": "candidate_order", "stored": comparable_stored, "replayed": replay_order})

        stored_actions = (original["portfolio_advice"] or {}).get("actions") or []
        if stored_actions:
            replay_actions = {
                item["symbol"]: {"action": item["action"], "target_weight": item["target_weight"]}
                for item in replay["portfolio"]["actions"]
            }
            for item in stored_actions:
                symbol = item.get("symbol")
                stored = {"action": item.get("action"), "target_weight": item.get("target_weight")}
                replayed = replay_actions.get(symbol)
                if replayed != stored:
                    diffs.append({"field": f"portfolio_action:{symbol}", "stored": stored, "replayed": replayed})

        stored_primary = (original["benchmark_route"] or {}).get("primary_benchmark")
        if stored_primary is not None:
            replayed_primary = replay["benchmark_route"].get("primary_benchmark")
            if stored_primary != replayed_primary:
                diffs.append({"field": "benchmark_primary", "stored": stored_primary, "replayed": replayed_primary})

        return diffs

    @staticmethod
    def _stored_candidate_order(candidates: list[dict]) -> list[str]:
        """落库候选顺序：全部带 rank 时按 rank 升序，否则按列表顺序。"""
        items = [item for item in candidates if isinstance(item, dict) and item.get("symbol")]
        if items and all(item.get("rank") is not None for item in items):
            items = sorted(items, key=lambda item: item["rank"])
        return [str(item["symbol"]) for item in items]

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))

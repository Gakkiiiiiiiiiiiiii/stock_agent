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
import inspect
from typing import Any

from contracts.decision_input import DecisionInputBundle
from contracts.decision_snapshot import DecisionSnapshotV3, canonical_hash
from contracts.replay import (
    COUNTERFACTUAL_OVERRIDE_KEYS,
    ReplayMode,
    ReplayRequest,
    ReplayResult,
    V2_REPLAY_MODES,
)
from engines.decision.benchmark_router import BenchmarkRouter
from engines.opportunity.service import OpportunityRankingService
from engines.portfolio.pipeline import run_portfolio_pipeline
from engines.versioning import get_version
from storage.bootstrap import create_all
from storage.repositories.market_feature_repository import MarketFeatureRepository
from storage.repositories.research_repository import DecisionRepository, DecisionSnapshotRepository
from storage.repositories.p2_repository import P2Repository
from storage.repositories.decision_input_repository import DecisionInputBundleRepository

REPLAY_MODES = (
    "original", "current", "multi_agent", "EXACT_REPLAY", "MODEL_REPLAY",
    "SKILL_REPLAY", "POLICY_REPLAY", "WORKFLOW_REPLAY", "COUNTERFACTUAL_REPLAY",
)

# 详细修改方案 §6：EXACT_REPLAY = 固定所有输入验证输出一致；
# COUNTERFACTUAL_REPLAY = 相同输入 + 新 policy/model/strategy 反事实分析。
_MODE_BASELINE = {
    "original": "original",
    "current": "current",
    "multi_agent": "multi_agent",
    "EXACT_REPLAY": "original",
    "COUNTERFACTUAL_REPLAY": "current",
}

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
        *,
        snapshot_repository: Any | None = None,
        bundle_repository: Any | None = None,
        model_runner: Any | None = None,
        skill_runner: Any | None = None,
        policy_runner: Any | None = None,
        workflow_runner: Any | None = None,
        exact_runner: Any | None = None,
        counterfactual_runner: Any | None = None,
    ) -> None:
        self.repository = repository or DecisionRepository()
        self.market_features = market_features or MarketFeatureRepository()
        self.snapshots = snapshot_repository or DecisionSnapshotRepository()
        self.bundles = bundle_repository or DecisionInputBundleRepository()
        self._runners = {
            "model": model_runner,
            "skill": skill_runner,
            "policy": policy_runner,
            "workflow": workflow_runner,
            "exact": exact_runner,
            "counterfactual": counterfactual_runner,
        }

    def replay(self, decision_id: str, mode: str = "original", overrides: dict | None = None, *, snapshot_id: str | None = None) -> dict:
        if mode not in REPLAY_MODES:
            return {"error": "INVALID_REPLAY_MODE", "decision_id": decision_id, "mode": mode, "supported_modes": list(REPLAY_MODES)}
        if mode in {item.value for item in V2_REPLAY_MODES}:
            # Existing v1 callers used the first implementation's EXACT and
            # COUNTERFACTUAL names without a v3 snapshot.  Preserve that
            # adapter only for those two names; every new component replay
            # fails explicitly when its immutable anchor is absent.
            try:
                snapshot = self._get_v3_snapshot(decision_id, snapshot_id=snapshot_id)
            except Exception as exc:
                return self._v2_rejected(decision_id, mode, "REPLAY_INPUT_INVALID", str(exc))
            if snapshot is not None:
                return self._replay_v2(decision_id, mode, overrides, snapshot, snapshot_id=snapshot_id)
            if mode not in {ReplayMode.EXACT_REPLAY.value, ReplayMode.COUNTERFACTUAL_REPLAY.value}:
                return self._v2_rejected(decision_id, mode, "REPLAY_SNAPSHOT_REQUIRED", "DecisionSnapshot v3 is required")
        create_all()
        decision = self.repository.get(decision_id)
        if decision is None:
            return {"error": "DECISION_NOT_FOUND", "decision_id": decision_id}

        baseline = _MODE_BASELINE[mode]
        counterfactual_overrides = dict(overrides or {}) if mode == "COUNTERFACTUAL_REPLAY" else None
        if mode == "COUNTERFACTUAL_REPLAY" and not counterfactual_overrides:
            return {"error": "COUNTERFACTUAL_OVERRIDE_REQUIRED", "decision_id": decision_id,
                    "hint": "反事实重放必须提供 override（policy_version / model / strategy 等）"}

        market_features, market_feature_source = self._resolve_market_features(decision, baseline)
        # §27：Replay 优先使用决策落库时的 DecisionSnapshot 版本锚点，而不是最新数据。
        decision_snapshot = self._load_decision_snapshot(decision_id)
        multi_agent = self._multi_agent_provenance(decision) if baseline == "multi_agent" else None
        if baseline == "multi_agent" and multi_agent and multi_agent.get("available"):
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
            if baseline == "original"
            else dict(current_versions)
        )

        original_output = {
            "candidates": list(decision.candidates or []),
            "portfolio_advice": dict(decision.portfolio_advice or {}),
            "benchmark_route": dict(decision.benchmark_route or {}),
        }
        diffs = self._diff(original_output, replay_output)
        if baseline == "multi_agent" and multi_agent and multi_agent.get("available"):
            recorded_regime = decision.market_regime
            replayed_regime = replay_output.get("multi_agent_context", {}).get("market_regime")
            if recorded_regime is not None and replayed_regime is not None and recorded_regime != replayed_regime:
                diffs.append({"field": "market_regime", "stored": recorded_regime, "replayed": replayed_regime})
            stored_risk_veto = self._stored_risk_veto(decision, multi_agent)
            replayed_risk_veto = replay_output.get("risk_veto")
            if stored_risk_veto is not None and replayed_risk_veto is not None and stored_risk_veto != replayed_risk_veto:
                diffs.append({"field": "risk_veto", "stored": stored_risk_veto, "replayed": replayed_risk_veto})

        result = {
            "decision_id": decision_id,
            "mode": mode,
            "input_versions": recorded_versions,
            "replay_versions": replay_versions,
            "version_mismatch": bool(mismatch_details),
            "version_mismatch_details": mismatch_details,
            "replay_uses_current_code": True,
            "market_feature_source": market_feature_source,
            "market_features": market_features,
            "decision_snapshot": decision_snapshot,
            "original_output": original_output,
            "replay_output": replay_output,
            "multi_agent": multi_agent,
            "match": not diffs,
            "diffs": diffs,
        }
        if counterfactual_overrides is not None:
            # §6：反事实分析：相同输入下“如果新规则当时存在，会做什么”。
            result["counterfactual"] = {
                "overrides": counterfactual_overrides,
                "applied_versions": dict(current_versions),
                "interpretation": "相同落库输入 + 当前/覆盖版本重算；diffs 表示新旧规则下的决策差异",
            }
        return self._json_safe(result)

    def replay_v2(self, request: ReplayRequest) -> dict:
        """Strict v2 entrypoint.

        Unlike the historical ``replay`` adapter this method never reads a
        decision row or deterministic fallback inputs.  A missing or invalid
        v3 snapshot is an explicit rejection.
        """
        if request.mode not in V2_REPLAY_MODES:
            return self._v2_rejected(request.decision_id, request.mode.value, "INVALID_REPLAY_MODE", "replay_v2 accepts v2 modes only")
        try:
            snapshot = self._get_v3_snapshot(request.decision_id, snapshot_id=request.snapshot_id)
        except Exception as exc:
            return self._v2_rejected(request.decision_id, request.mode.value, "REPLAY_INPUT_INVALID", str(exc), snapshot_id=request.snapshot_id)
        if snapshot is None:
            return self._v2_rejected(request.decision_id, request.mode.value, "REPLAY_SNAPSHOT_REQUIRED", "DecisionSnapshot v3 is required", snapshot_id=request.snapshot_id)
        return self._replay_v2(request.decision_id, request.mode.value, dict(request.overrides), snapshot, snapshot_id=request.snapshot_id)

    # ---- Replay v2 ---------------------------------------------------------

    def _get_v3_snapshot(self, decision_id: str, *, snapshot_id: str | None = None) -> DecisionSnapshotV3 | None:
        """Load only the persisted v3 anchor; no mutable data source is consulted."""
        getter = getattr(self.snapshots, "get_v3" if snapshot_id else "get_v3_for_decision", None)
        if getter is None:
            return None
        item = getter(snapshot_id or decision_id)
        if item is None:
            return None
        parsed = item if isinstance(item, DecisionSnapshotV3) else DecisionSnapshotV3.model_validate(item)
        if parsed.decision_id != decision_id:
            raise ValueError("snapshot does not belong to the requested decision")
        return parsed

    def _replay_v2(self, decision_id: str, mode: str, overrides: dict | None, snapshot: DecisionSnapshotV3, *, snapshot_id: str | None = None) -> dict:
        try:
            request = ReplayRequest(decision_id=decision_id, mode=mode, overrides=overrides or {}, snapshot_id=snapshot_id)
        except Exception as exc:
            return self._v2_rejected(decision_id, mode, "INVALID_REPLAY_REQUEST", str(exc))
        try:
            bundle = self._validate_v2_anchor(request, snapshot)
            baseline = self._v2_segments(snapshot, bundle)
            if request.mode is ReplayMode.EXACT_REPLAY:
                runner = self._runners.get("exact")
                if runner is None:
                    return self._v2_success(request, snapshot, bundle, baseline, baseline, (), verification="INTEGRITY_ONLY")
                result = self._invoke_runner(runner, component="exact", bundle=bundle, snapshot=snapshot, baseline=baseline, replacement=None, request=request)
                self._validate_runner_result(request.mode, result, baseline, replacement=None, component="exact")
                replayed = self._apply_runner_result("exact", baseline, result, None)
                return self._v2_success(request, snapshot, bundle, baseline, replayed, (), verification="FULL_REPLAY", require_match=True)

            if request.mode is ReplayMode.COUNTERFACTUAL_REPLAY:
                replayed, changed = self._run_counterfactual(request, snapshot, bundle, baseline)
            else:
                component = {
                    ReplayMode.MODEL_REPLAY: "model",
                    ReplayMode.SKILL_REPLAY: "skill",
                    ReplayMode.POLICY_REPLAY: "policy",
                    ReplayMode.WORKFLOW_REPLAY: "workflow",
                }[request.mode]
                runner = self._runners.get(component)
                if runner is None:
                    return self._v2_rejected(request.decision_id, request.mode.value, "REPLAY_RUNNER_REQUIRED", f"runner required for {component}")
                replacement = request.overrides[component]
                result = self._invoke_runner(
                    runner,
                    component=component,
                    bundle=bundle,
                    snapshot=snapshot,
                    baseline=baseline,
                    replacement=replacement,
                    request=request,
                )
                self._validate_runner_result(request.mode, result, baseline, replacement=replacement, component=component)
                replayed = self._apply_runner_result(component, baseline, result, replacement)
                changed = {
                    "model": "model_identity",
                    "skill": "skill_identity",
                    "policy": "policy_version",
                    "workflow": "workflow_version",
                }[component],
                self._ensure_fixed_segments(request.mode, baseline, replayed)
            return self._v2_success(request, snapshot, bundle, baseline, replayed, changed)
        except Exception as exc:
            # Replay callers receive an auditable rejection rather than a
            # partially fabricated decision when a persisted anchor is bad.
            return self._v2_rejected(request.decision_id, request.mode.value, "REPLAY_INPUT_INVALID", str(exc), snapshot_id=snapshot.snapshot_id)

    def _validate_v2_anchor(self, request: ReplayRequest, snapshot: DecisionSnapshotV3) -> DecisionInputBundle:
        if request.snapshot_id is not None and request.snapshot_id != snapshot.snapshot_id:
            raise ValueError("requested snapshot_id does not match the decision anchor")
        ref = snapshot.input_bundle
        bundle = DecisionInputBundle.model_validate(ref.payload)
        if bundle.bundle_id != ref.bundle_id or bundle.bundle_hash != ref.bundle_hash:
            raise ValueError("snapshot input bundle reference is inconsistent")
        persisted_getter = getattr(self.bundles, "get_bundle", None)
        if persisted_getter is not None:
            persisted = persisted_getter(ref.bundle_id)
            if persisted is None:
                raise ValueError("DecisionInputBundle is missing from persistence")
            persisted_bundle = persisted if isinstance(persisted, DecisionInputBundle) else DecisionInputBundle.model_validate(persisted)
            if persisted_bundle.bundle_hash != bundle.bundle_hash or persisted_bundle.model_dump(mode="json") != bundle.model_dump(mode="json"):
                raise ValueError("persisted DecisionInputBundle does not match snapshot")

        lineage = [item for item in snapshot.lineage if isinstance(item, dict)]
        bundle_lineage = [item for item in lineage if str(item.get("type", "")).upper() in {"BUNDLE", "DECISION_INPUT_BUNDLE"}]
        if not any(item.get("id") == bundle.bundle_id for item in bundle_lineage):
            raise ValueError("snapshot lineage does not contain the input bundle")
        for item in bundle_lineage:
            if item.get("id") == bundle.bundle_id and item.get("hash") not in (None, bundle.bundle_hash):
                raise ValueError("snapshot bundle lineage hash mismatch")

        self._validate_snapshot_hashes(snapshot, bundle)
        return bundle

    @staticmethod
    def _validate_snapshot_hashes(snapshot: DecisionSnapshotV3, bundle: DecisionInputBundle) -> None:
        """Recheck nested hashes even when a repository returns a raw payload."""
        evidence = snapshot.evidence
        refs = evidence.get("refs") or []
        hashes = evidence.get("hashes") or {}
        payloads = evidence.get("payloads") or {}
        for ref in refs:
            ref_id = ref.get("id") if isinstance(ref, dict) else ref
            if not ref_id or ref_id not in hashes:
                raise ValueError(f"snapshot evidence hash missing: {ref_id}")
            if ref_id in payloads and canonical_hash(payloads[ref_id]) != hashes[ref_id]:
                raise ValueError(f"snapshot evidence hash mismatch: {ref_id}")
        bundle_ids = {item.evidence_id for item in bundle.evidence}
        snapshot_ids = {ref.get("id") if isinstance(ref, dict) else ref for ref in refs}
        if bundle_ids != snapshot_ids:
            raise ValueError("snapshot evidence refs do not match the input bundle")

        specialists = snapshot.specialists
        refs = specialists.get("artifact_refs") or []
        hashes = specialists.get("artifact_hashes") or {}
        payloads = specialists.get("payloads") or {}
        for ref in refs:
            ref_id = ref.get("id") if isinstance(ref, dict) else ref
            if not ref_id or ref_id not in hashes:
                raise ValueError(f"snapshot specialist hash missing: {ref_id}")
            if ref_id in payloads and canonical_hash(payloads[ref_id]) != hashes[ref_id]:
                raise ValueError(f"snapshot specialist hash mismatch: {ref_id}")

    @staticmethod
    def _v2_segments(snapshot: DecisionSnapshotV3, bundle: DecisionInputBundle) -> dict[str, Any]:
        proposal_payload = snapshot.proposal.get("payload")
        final = snapshot.output.get("final_decision")
        policy_evaluation = snapshot.policy
        model_identity = snapshot.model
        skill_identity = snapshot.skill
        workflow_version = snapshot.runtime.get("workflow_version")
        return {
            "bundle": bundle.model_dump(mode="json"),
            "evidence": snapshot.evidence,
            # Explicit names prevent an evaluation payload from being
            # mistaken for the fixed identity/version anchor.
            "model_identity": model_identity,
            "skill_identity": skill_identity,
            "policy_version": snapshot.policy.get("policy_version"),
            "workflow_version": workflow_version,
            "policy_evaluation": policy_evaluation,
            "model": model_identity,
            "skill": skill_identity,
            "policy": policy_evaluation,
            "workflow": workflow_version,
            "runtime": snapshot.runtime,
            "specialists": snapshot.specialists,
            "conflicts": snapshot.conflicts,
            "proposal": proposal_payload,
            "proposal_hash": snapshot.proposal.get("proposal_hash"),
            "output": snapshot.output,
            "final": final,
            "fixed_fields": (),
        }

    def _run_counterfactual(self, request: ReplayRequest, snapshot: DecisionSnapshotV3, bundle: DecisionInputBundle, baseline: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
        unknown = set(request.overrides) - COUNTERFACTUAL_OVERRIDE_KEYS
        if unknown:
            raise ValueError(f"unsupported counterfactual override(s): {sorted(unknown)}")
        runner = self._runners.get("counterfactual")
        if runner is None:
            raise ValueError("runner required for COUNTERFACTUAL_REPLAY")
        result = self._invoke_runner(
            runner, component="counterfactual", bundle=bundle, snapshot=snapshot,
            baseline=baseline, replacement=dict(request.overrides), request=request,
        )
        self._validate_runner_result(request.mode, result, baseline)
        applied = result.get("applied_overrides", result.get("overrides"))
        if applied != dict(request.overrides):
            raise ValueError("counterfactual runner applied overrides do not match request")
        replayed = self._apply_runner_result("counterfactual", baseline, result, dict(request.overrides))
        replayed["counterfactual_overrides"] = dict(request.overrides)
        for key, value in request.overrides.items():
            replayed[key] = value
        self._ensure_fixed_segments(request.mode, baseline, replayed)
        return replayed, tuple(sorted(request.overrides))

    @staticmethod
    def _invoke_runner(runner: Any, *, component: str, bundle: DecisionInputBundle, snapshot: DecisionSnapshotV3, baseline: dict[str, Any], replacement: Any, request: ReplayRequest) -> Any:
        """Call a pure injected runner while supporting concise test callables."""
        kwargs = {
            "bundle": bundle,
            "input_bundle": bundle,
            "snapshot": snapshot,
            "baseline": baseline,
            "replacement": replacement,
            "proposal": snapshot.proposal,
            "model": baseline["model"],
            "skill": baseline["skill"],
            "policy": baseline["policy"],
            "workflow": baseline["workflow"],
            "request": request,
            "overrides": request.overrides,
        }
        # Put the selected replacement after the fixed baseline values.  This
        # is important for concise callables using ``model=``/``policy=``.
        if component in {"model", "skill", "policy", "workflow"}:
            kwargs[component] = replacement
        if component == "counterfactual":
            kwargs.update(request.overrides)
        try:
            signature = inspect.signature(runner)
        except (TypeError, ValueError):
            return runner(bundle, replacement)
        accepts_kwargs = any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        if not accepts_kwargs:
            kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
        return runner(**kwargs)

    @classmethod
    def _validate_runner_result(cls, mode: ReplayMode, result: Any, baseline: dict[str, Any], *, replacement: Any = None, component: str | None = None) -> None:
        if not isinstance(result, dict):
            raise ValueError(f"{mode.value} runner must return a structured JSON object")
        has_policy = "policy_evaluation" in result or "policy" in result
        has_final = any(key in result for key in ("final", "output", "final_decision"))
        if has_policy:
            policy = result.get("policy_evaluation", result.get("policy"))
            if not isinstance(policy, dict) or not policy.get("policy_version"):
                raise ValueError(f"{mode.value} runner policy_evaluation must include policy_version")
        if has_final:
            final = result.get("final", result.get("output", result.get("final_decision")))
            if not isinstance(final, dict) or not final:
                raise ValueError(f"{mode.value} runner final/output must be a non-empty object")
        if mode in {ReplayMode.EXACT_REPLAY, ReplayMode.MODEL_REPLAY, ReplayMode.SKILL_REPLAY}:
            if not isinstance(result.get("proposal"), dict) or not result["proposal"] or not has_policy or not has_final:
                raise ValueError(f"{mode.value} runner must return proposal, policy_evaluation, and final/output")
            if mode in {ReplayMode.MODEL_REPLAY, ReplayMode.SKILL_REPLAY} and result.get("policy_evaluation", result.get("policy", {})).get("policy_version") != baseline.get("policy_version"):
                raise ValueError(f"{mode.value} cannot change fixed policy_version")
        elif mode is ReplayMode.POLICY_REPLAY:
            if not has_policy or not has_final:
                raise ValueError("POLICY_REPLAY runner must return policy_evaluation and final/output")
            if "proposal" in result and cls._proposal_hash(result["proposal"]) != baseline.get("proposal_hash"):
                raise ValueError("POLICY_REPLAY cannot change the fixed Proposal hash")
        elif mode is ReplayMode.WORKFLOW_REPLAY:
            if not ("specialists" in result or "artifacts" in result) or "conflicts" not in result or not isinstance(result.get("proposal"), dict) or not result["proposal"] or not has_policy or not has_final:
                raise ValueError("WORKFLOW_REPLAY runner must return artifacts, conflicts, proposal, policy_evaluation, and final/output")
            if result.get("policy_evaluation", result.get("policy", {})).get("policy_version") != baseline.get("policy_version"):
                raise ValueError("WORKFLOW_REPLAY cannot change fixed policy_version")
        elif mode is ReplayMode.COUNTERFACTUAL_REPLAY:
            if not has_final or not ("applied_overrides" in result or "overrides" in result):
                raise ValueError("COUNTERFACTUAL_REPLAY runner must return applied_overrides and final/output")
        if component in {"model", "skill", "workflow", "policy"}:
            cls._validate_runner_identity(mode, component, result, replacement)

    @classmethod
    def _validate_runner_identity(cls, mode: ReplayMode, component: str, result: dict[str, Any], replacement: Any) -> None:
        """Runner output may echo identity, but may not replace the request."""
        expected = cls._canonical_identity(replacement, component)
        if component in {"model", "skill"}:
            names = (f"{component}_identity", component)
            supplied = next((result[name] for name in names if name in result), None)
            if supplied is not None and cls._canonical_identity(supplied, component) != expected:
                raise ValueError(f"{mode.value} runner {component}_identity differs from requested replacement")
        elif component == "workflow":
            supplied = result.get("workflow_version", result.get("workflow"))
            if supplied is not None and cls._canonical_identity(supplied, component) != expected:
                raise ValueError("WORKFLOW_REPLAY runner workflow_version differs from requested replacement")
        else:
            supplied = result.get("policy_version")
            if supplied is None:
                policy = result.get("policy_evaluation", result.get("policy"))
                supplied = policy.get("policy_version") if isinstance(policy, dict) else None
            if supplied is not None and cls._canonical_identity(supplied, component) != expected:
                raise ValueError("POLICY_REPLAY runner policy_version differs from requested replacement")

    @staticmethod
    def _canonical_identity(value: Any, component: str) -> str:
        if isinstance(value, dict):
            key = {"policy": "policy_version", "workflow": "workflow_version"}.get(component)
            if key and key in value:
                value = value[key]
            elif component in {"model", "skill"} and "identity" in value:
                value = value["identity"]
        return canonical_hash(value)

    @staticmethod
    def _proposal_hash(value: Any) -> str:
        payload = value.get("payload") if isinstance(value, dict) and "payload" in value else value
        return canonical_hash(payload)

    @staticmethod
    def _apply_runner_result(component: str, baseline: dict[str, Any], result: Any, replacement: Any) -> dict[str, Any]:
        replayed = dict(baseline)
        replayed[component] = replacement if replacement is not None else baseline.get(component)
        if component == "model":
            replayed["model_identity"] = replayed["model"] = replacement
        elif component == "skill":
            replayed["skill_identity"] = replayed["skill"] = replacement
        elif component == "policy":
            replayed["policy_version"] = replacement.get("policy_version") if isinstance(replacement, dict) else replacement
        elif component == "workflow":
            replayed["workflow_version"] = replayed["workflow"] = replacement
        if not isinstance(result, dict):
            replayed["output"] = result
            return replayed
        # Runners may return a structured execution envelope or a direct
        # proposal/final output.  Identity remains the requested replacement;
        # emitted artifacts are compared separately and never relabelled as
        # the model/policy identity.
        for key in ("model", "skill", "model_identity", "skill_identity", "policy", "policy_evaluation", "policy_version", "workflow", "workflow_version", "runtime", "proposal", "output", "final", "specialists", "artifacts", "conflicts", "applied_overrides"):
            if key in result:
                replayed[key] = result[key]
        if "artifacts" in result and "specialists" not in result:
            replayed["specialists"] = result["artifacts"]
        if "policy_evaluation" in result and "policy" not in result:
            replayed["policy"] = result["policy_evaluation"]
        if "policy_evaluation" in result:
            replayed["policy_evaluation"] = result["policy_evaluation"]
        if isinstance(replayed.get("policy"), dict) and replayed["policy"].get("policy_version") is not None:
            replayed["policy_version"] = replayed["policy"]["policy_version"]
        if "model_identity" in result:
            replayed["model"] = replayed["model_identity"]
        if "skill_identity" in result:
            replayed["skill"] = replayed["skill_identity"]
        if "workflow_version" in result:
            replayed["workflow"] = replayed["workflow_version"]
        if "proposal" in result:
            replayed["proposal_hash"] = DecisionReplayService._proposal_hash(result["proposal"])
        if "output" in result and isinstance(result["output"], dict) and "final_decision" in result["output"]:
            replayed["final"] = result["output"]["final_decision"]
        elif "final_decision" in result:
            replayed["output"] = result
            replayed["final"] = result["final_decision"]
        if not any(key in result for key in ("model", "skill", "policy", "workflow", "runtime", "proposal", "output", "final", "specialists", "conflicts", "final_decision")):
            if any(key in result for key in ("action", "approved", "final_decision", "decision")):
                replayed["output"] = result
            elif component == "counterfactual":
                replayed["output"] = result
        # Keep the request replacement as the canonical identity.  Any
        # echoed identity was checked for equivalence before this method.
        if component == "model":
            replayed["model_identity"] = replayed["model"] = replacement
        elif component == "skill":
            replayed["skill_identity"] = replayed["skill"] = replacement
        elif component == "policy":
            replayed["policy_version"] = replacement.get("policy_version") if isinstance(replacement, dict) else replacement
        elif component == "workflow":
            replayed["workflow_version"] = replayed["workflow"] = replacement
        return replayed

    @staticmethod
    def _ensure_fixed_segments(mode: ReplayMode, baseline: dict[str, Any], replayed: dict[str, Any]) -> None:
        fixed_by_mode = {
            ReplayMode.EXACT_REPLAY: ("bundle", "evidence", "model_identity", "skill_identity", "policy_version", "workflow_version"),
            ReplayMode.MODEL_REPLAY: ("bundle", "evidence", "skill_identity", "policy_version", "workflow_version"),
            ReplayMode.SKILL_REPLAY: ("bundle", "evidence", "model_identity", "policy_version", "workflow_version"),
            ReplayMode.POLICY_REPLAY: ("bundle", "evidence", "model_identity", "skill_identity", "workflow_version", "proposal_hash"),
            ReplayMode.WORKFLOW_REPLAY: ("bundle", "evidence", "model_identity", "skill_identity", "policy_version"),
            ReplayMode.COUNTERFACTUAL_REPLAY: ("bundle", "evidence", "model_identity", "skill_identity", "workflow_version"),
        }
        for field in fixed_by_mode[mode]:
            if baseline.get(field) != replayed.get(field):
                raise ValueError(f"replay runner changed fixed field: {field}")

    @staticmethod
    def _v2_success(request: ReplayRequest, snapshot: DecisionSnapshotV3, bundle: DecisionInputBundle, baseline: dict[str, Any], replayed: dict[str, Any], changed: tuple[str, ...] | list[str], *, verification: str = "FULL_REPLAY", require_match: bool = False) -> dict:
        fixed_by_mode = {
            ReplayMode.EXACT_REPLAY: ("bundle", "evidence", "model_identity", "skill_identity", "policy_version", "workflow_version"),
            ReplayMode.MODEL_REPLAY: ("bundle", "evidence", "skill_identity", "policy_version", "workflow_version"),
            ReplayMode.SKILL_REPLAY: ("bundle", "evidence", "model_identity", "policy_version", "workflow_version"),
            ReplayMode.POLICY_REPLAY: ("bundle", "evidence", "model_identity", "skill_identity", "workflow_version", "proposal_hash"),
            ReplayMode.WORKFLOW_REPLAY: ("bundle", "evidence", "model_identity", "skill_identity", "policy_version"),
            ReplayMode.COUNTERFACTUAL_REPLAY: ("bundle", "evidence", "model_identity", "skill_identity", "workflow_version"),
        }
        selected = tuple(field for field in changed if field not in baseline or baseline.get(field) != replayed.get(field))
        candidate_fields = ("model_identity", "skill_identity", "policy_version", "workflow_version", "policy_evaluation", "runtime", "specialists", "conflicts", "proposal_hash", "proposal", "output", "final", "market_regime", "risk_parameter", "portfolio_state", "strategy")
        changed_fields = tuple(dict.fromkeys((*selected, *(field for field in candidate_fields if field in replayed and field in baseline and field not in selected and baseline.get(field) != replayed.get(field)))))
        # An EXACT runner may reveal a mismatch in an identity that was
        # expected to be fixed.  Keep that field in the diff rather than
        # raising a generic fixed-segment exception; the audit result then
        # shows precisely what diverged.
        fixed = tuple(field for field in fixed_by_mode[request.mode] if field not in changed_fields)
        diffs = tuple(
            {"field": field, "baseline": baseline.get(field), "replayed": replayed.get(field), "changed": baseline.get(field) != replayed.get(field)}
            for field in changed_fields
        )
        is_match = not any(item["changed"] for item in diffs)
        result = ReplayResult(
            decision_id=request.decision_id,
            snapshot_id=snapshot.snapshot_id,
            mode=request.mode,
            status="REJECTED" if require_match and not is_match else "SUCCEEDED",
            match=is_match,
            verification=verification,
            counterfactual=request.mode is ReplayMode.COUNTERFACTUAL_REPLAY,
            bundle_id=bundle.bundle_id,
            bundle_hash=bundle.bundle_hash,
            fixed_fields=fixed,
            changed_fields=changed_fields,
            diffs=diffs,
            output={
                "baseline": baseline["output"],
                "replayed": replayed,
                "integrity_verified": True,
                "replay_executed": verification == "FULL_REPLAY",
            },
            error="EXACT_REPLAY_MISMATCH" if require_match and not is_match else None,
            detail="recomputed snapshot artifacts differ from the persisted output" if require_match and not is_match else None,
        )
        return result.model_dump(mode="json")

    @staticmethod
    def _v2_rejected(decision_id: str, mode: str, error: str, detail: str, *, snapshot_id: str | None = None) -> dict:
        try:
            parsed_mode = ReplayMode(mode)
        except ValueError:
            parsed_mode = ReplayMode.EXACT_REPLAY
        return ReplayResult(decision_id=decision_id, snapshot_id=snapshot_id, mode=parsed_mode, status="REJECTED", match=False, error=error, detail=detail).model_dump(mode="json")

    def _load_decision_snapshot(self, decision_id: str) -> dict | None:
        snapshot = self.snapshots.get_for_decision(decision_id)
        if snapshot is None:
            return None
        return {
            "snapshot_id": snapshot.snapshot_id,
            "decision_time": snapshot.decision_time,
            "schema_version": snapshot.schema_version,
            "market": dict(snapshot.market or {}),
            "content": dict(snapshot.content or {}),
            "factor": dict(snapshot.factor or {}),
            "strategy": dict(snapshot.strategy or {}),
            "agent": dict(snapshot.agent or {}),
            "model": dict(snapshot.model or {}),
            "runtime": dict(snapshot.runtime or {}),
            "tools": dict(snapshot.tools or {}),
            "inputs": dict(snapshot.inputs or {}),
            "proposal": dict(snapshot.proposal or {}),
            "policy": dict(snapshot.policy or {}),
            "output": dict(snapshot.output or {}),
            "portfolio": dict(snapshot.portfolio or {}),
            "risk": dict(snapshot.risk or {}),
            "lineage": list(snapshot.lineage or []),
            "decision_quality": snapshot.decision_quality,
        }

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

"""LLM 驱动的自动因子挖掘。

流程：组装 DSL 说明与已有因子表现 → 请求 LLM 生成候选 RPN 公式 →
虚拟机校验可计算性 → 适应度打分 → 达标且非重复者入库 → 结果反馈进下一轮 prompt。
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Callable
from uuid import uuid4

import numpy as np
import yaml

from engines.factor import fitness as fitness_mod
from engines.factor.lookback import max_lookback_from_rpn
from engines.factor.oos_audit import append_oos_audit
from engines.factor.purged_walkforward import run_purged_walkforward
from engines.factor.research_split import FactorResearchSplit, build_research_split
from engines.factor.library import (
    active_factors,
    add_factor,
    is_duplicate,
    load_library,
    research_validated_factors,
    save_library,
)
from engines.factor.vocab import (
    BINARY_OPS,
    CS_OPS,
    FEATURES,
    MAX_FORMULA_TOKENS,
    TERNARY_OPS,
    TS_BINARY_OPS,
    TS_OPS,
    TS_WINDOWS,
    UNARY_OPS,
    is_valid_token,
)
from engines.factor.vm import StackVM
from financial_agent.research_config import EvaluationConfig, get_research_config
from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

_DEFAULT_ROUNDS = 3
_DEFAULT_CANDIDATES = 8
_DEFAULT_MAX_CANDIDATES = 40  # 单次挖掘评估候选总数预算（env FACTOR_MINING_MAX_CANDIDATES）

# 饱和早停：连续 2 轮无入库且判重拒绝率 > 0.5 时判定搜索空间饱和
_EARLY_STOP_ROUNDS = 2
_EARLY_STOP_DUP_RATE = 0.5

# 多重检验收紧：累计评估候选数超过 30 后 rank_ic 门槛 ×1.5（简化 Bonferroni 校正，
# 只在此处生效，不修改 fitness.py 的基础阈值常量）
_BONFERRONI_EVAL_COUNT = 30
_BONFERRONI_FACTOR = 1.5


@dataclass
class DiscoveryCandidate:
    rpn: list[str]
    hypothesis: str
    discovery_metrics: dict
    discovery_values: np.ndarray
    full_values: np.ndarray
    candidate_hash: str
    round_index: int
    evaluated_index: int
    lookback: int


def evaluate_oos_splits(
    factor_panel: np.ndarray,
    closes: np.ndarray,
    eval_start: int,
    eval_end: int,
    horizon: int = 5,
) -> dict:
    """Default OOS gate for mined factors: purged walk-forward with embargo."""
    return run_purged_walkforward(factor_panel, closes, eval_start=eval_start, eval_end=eval_end, horizon=horizon)


def _rank_ic_threshold(evaluated: int, base_threshold: float) -> float:
    """按累计评估候选数返回当前 rank_ic 入库门槛。"""
    if evaluated > _BONFERRONI_EVAL_COUNT:
        return base_threshold * _BONFERRONI_FACTOR
    return base_threshold

_EXAMPLES = [
    {
        "hypothesis": "5日反转：短期跌幅大的股票未来反弹概率高",
        "rpn": ["close", "close", "ts_delay_5", "div", "cs_rank"],
    },
    {
        "hypothesis": "量价背离：量能低于20日均量时上涨乏力（做空向，取负号）",
        "rpn": ["volume", "volume", "ts_mean_20", "div", "neg", "cs_rank"],
    },
    {
        "hypothesis": "波动率择时：低波动股票未来收益更稳",
        "rpn": ["ret", "ts_std_20", "neg", "cs_rank"],
    },
]

_SEED_CONFIG = "config/factor_seed_alpha191.yaml"


def _load_seed_entries() -> list[dict]:
    """读取 Alpha191 风格种子库（few-shot 示例 + 判重基线），失败时回退内置示例。"""
    path = project_root() / _SEED_CONFIG
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = [
            {"name": str(s.get("name") or ""), "hypothesis": str(s.get("hypothesis") or ""),
             "rpn": [str(t) for t in s.get("rpn") or []]}
            for s in data.get("seeds") or []
            if s.get("rpn")
        ]
        if entries:
            return entries
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取因子种子库失败 %s: %s", path, exc)
    return [{"name": "", "hypothesis": e["hypothesis"], "rpn": e["rpn"]} for e in _EXAMPLES]


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)


def _parse_json_array(text: str) -> list[dict]:
    """从 LLM 输出中解析 JSON 数组（兼容 ```json 代码块）。"""
    text = (text or "").strip()
    match = _JSON_BLOCK_RE.search(text)
    if match:
        text = match.group(1)
    else:
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


class FactorMiner:
    def __init__(self, model_client=None, library_path: str | None = None):
        if model_client is None:
            from app.model_providers import AnalysisModelClient

            model_client = AnalysisModelClient()
        self.model_client = model_client
        self.library_path = library_path
        self.vm = StackVM()

    def _build_prompt(
        self,
        library: dict,
        candidates_per_round: int,
        horizon: int,
        round_index: int,
        last_round_results: list[dict] | None,
        evaluation: EvaluationConfig,
    ) -> str:
        ts_tokens = [f"{op}_{w}" for op in TS_OPS for w in TS_WINDOWS]
        ts_binary_tokens = [f"{op}_{w}" for op in TS_BINARY_OPS for w in TS_WINDOWS]
        seed_examples = [{"hypothesis": s["hypothesis"], "rpn": s["rpn"]} for s in _load_seed_entries()]
        top_factors = active_factors(library, limit=5)
        top_desc = (
            json.dumps([
                {"rpn": f["rpn"], "hypothesis": f.get("hypothesis", ""), "metrics": _prompt_safe_metrics(f.get("metrics", {}))}
                for f in top_factors
            ], ensure_ascii=False, indent=1)
            if top_factors else "（暂无）"
        )
        last_desc = (
            json.dumps(last_round_results, ensure_ascii=False, indent=1)
            if last_round_results else "（首轮，无反馈）"
        )
        return f"""你是一名量化研究员，正在为 A 股横截面选股挖掘 alpha 因子。

## 因子 DSL（逆波兰表达式 RPN）
- 特征: {list(FEATURES)}
  （面板形状为 标的×交易日；ret 为日收益，vwap=amount/volume；
  event_heat/theme_sentiment 为视频知识库特征，衡量近期视频提及热度与多空倾向，信号弱、只作辅助维度）
- 时序算子(一元，沿时间轴): {ts_tokens}
- 时序算子(二元，沿时间轴): {ts_binary_tokens}
- 横截面算子(逐日截面): {list(CS_OPS)}
- 一元算子: {list(UNARY_OPS)}
- 二元算子: {list(BINARY_OPS)}
- 三元算子: {list(TERNARY_OPS)}（where(cond,a,b)：cond>0 取 a，否则取 b）
- 规则: 公式总长度 ≤ {MAX_FORMULA_TOKENS} 个 token；必须以 cs_rank/cs_zscore/cs_demean 之一收尾以保证截面可比；
  不能用数值常量，窗口已烘焙在算子名中。
  二元算子需要两个操作数：若两个操作数都源自同一特征，需把该特征名连续压栈两次（见示例 5日反转）。

## 经典示例（Alpha191 风格种子，可在其思路上变异，但不要照抄）
{json.dumps(seed_examples, ensure_ascii=False, indent=1)}

## 评估口径（样本内，预测未来 {horizon} 日收益）
入库门槛:
- coverage >= {evaluation.min_coverage}
- rank_ic >= {evaluation.min_rank_ic}
- icir >= {evaluation.min_icir}
- topk_excess_annual_return > {evaluation.min_topk_excess_annual_return}
综合 fitness = 5*rank_ic + 0.5*icir + topk_excess_annual_return
（topk_excess_annual_return 为 TopK 组合相对等权基准的超额年化收益）。

## 因子库当前 Top5（避免重复，可在其思路上变异）
{top_desc}

## 上一轮候选的评估反馈（利用它改进方向）
{last_desc}

## 任务（第 {round_index} 轮）
生成 {candidates_per_round} 个新候选因子，覆盖不同思路（反转/动量/量价/波动率/换手等）。
只输出 JSON 数组，不要输出其他文字：
[{{"rpn": ["token1", "token2", ...], "hypothesis": "一句话经济含义"}}, ...]"""

    def _validate_candidate(self, item: dict) -> tuple[list[str] | None, str]:
        rpn = item.get("rpn")
        hypothesis = str(item.get("hypothesis") or "").strip()
        if not isinstance(rpn, list) or not rpn or len(rpn) > MAX_FORMULA_TOKENS:
            return None, hypothesis
        rpn = [str(t) for t in rpn]
        if not all(is_valid_token(t) for t in rpn):
            return None, hypothesis
        if rpn[-1] not in CS_OPS:
            return None, hypothesis
        return rpn, hypothesis

    def mine(
        self,
        panel: dict[str, np.ndarray],
        symbols: list[str],
        rounds: int | None = None,
        candidates_per_round: int | None = None,
        horizon: int | None = None,
        eval_window: int | None = None,
        dates: list[str] | None = None,
        lease_guard: Callable[[], None] | None = None,
        data_version: str | None = None,
        data_snapshot_id: str | None = None,
    ) -> dict:
        """执行挖掘，返回 {accepted, rejected, warning, stopped_early, stop_reason, evaluated} 摘要。"""
        rounds = rounds or int(os.getenv("FACTOR_MINING_ROUNDS", _DEFAULT_ROUNDS))
        candidates_per_round = candidates_per_round or int(
            os.getenv("FACTOR_MINING_CANDIDATES_PER_ROUND", _DEFAULT_CANDIDATES))
        research_config = get_research_config()
        horizon = horizon or research_config.evaluation.horizon_days
        max_candidates = int(os.getenv("FACTOR_MINING_MAX_CANDIDATES", _DEFAULT_MAX_CANDIDATES))

        if not self.model_client or not self.model_client.available():
            return {"accepted": [], "rejected": [], "warning": "挖掘模型不可用，请配置 ANALYSIS_MODEL_*",
                    "stopped_early": False, "stop_reason": None, "evaluated": 0}
        closes = panel.get("close")
        if closes is None or not symbols:
            return {"accepted": [], "rejected": [], "warning": "特征面板为空，无法挖掘",
                    "stopped_early": False, "stop_reason": None, "evaluated": 0}
        split = build_research_split(closes.shape[1], research_config.data_split, horizon)
        discovery_eval_start = 0
        discovery_eval_end = 0
        if split is not None:
            discovery_eval_end = split.discovery_end
            discovery_eval_start = (
                split.discovery_start
                if eval_window is None
                else max(split.discovery_start, discovery_eval_end - int(eval_window))
            )
        diagnostics = {
            "discovery_days": split.discovery_days if split else 0,
            "final_oos_days": split.final_oos_days if split else 0,
            "warmup_days": split.discovery_warmup_days if split else 0,
            "configured_eval_window": eval_window,
            "discovery_eval_start": discovery_eval_start,
            "discovery_eval_end": discovery_eval_end,
            "actual_discovery_eval_days": max(0, discovery_eval_end - discovery_eval_start),
            "split_ranges": split.diagnostics(horizon, closes.shape[1]) if split else {},
            "oos_window_count": 0,
            "run_valid": True,
            "run_failure_code": None,
        }
        if split is None:
            return {"accepted": [], "rejected": [], "warning": "样本长度不足，无法拆分 discovery/final OOS",
                    "stopped_early": False, "stop_reason": None, "evaluated": 0,
                    "diagnostics": diagnostics | {"run_valid": False, "run_failure_code": "SAMPLE_SPLIT_UNAVAILABLE"}}

        library = load_library(self.library_path)
        accepted: list[dict] = []
        rejected: list[dict] = []
        discovery_candidates: list[DiscoveryCandidate] = []
        last_round_results: list[dict] | None = None
        model_name = getattr(self.model_client, "model", "") or ""
        evaluated = 0                       # 已跑 VM/打分的候选总数（预算与门槛收紧的计数基准）
        rejected_rpn: set[tuple[str, ...]] = set()  # 评估过但被拒绝的公式缓存，后续轮次直接判重复
        consecutive_empty = 0               # 连续「无入库且高重复率」的轮数
        stopped_early = False
        stop_reason: str | None = None

        # 预计算库内 active 因子面板用于相关性判重
        active_panels: dict[str, np.ndarray] = {}
        for factor in research_validated_factors(library):
            panel_values = self.vm.execute(factor.get("rpn") or [], panel)
            if panel_values is not None:
                active_panels[factor["id"]] = panel_values[:, discovery_eval_start:discovery_eval_end]
        # Alpha191 种子面板进判重池：引导 LLM 在种子思路上变异而非照抄
        for seed in _load_seed_entries():
            panel_values = self.vm.execute(seed["rpn"], panel)
            if panel_values is not None:
                active_panels[f"SEED:{seed['name'] or seed['hypothesis'][:12]}"] = panel_values[:, discovery_eval_start:discovery_eval_end]

        for round_index in range(1, rounds + 1):
            prompt = self._build_prompt(library, candidates_per_round, horizon, round_index, last_round_results, research_config.evaluation)
            try:
                response = self.model_client.complete(prompt, temperature=0.8)
                content = (response or {}).get("content", "")
            except Exception as exc:  # noqa: BLE001
                logger.warning("第 %s 轮 LLM 请求失败: %s", round_index, exc)
                last_round_results = [{"error": f"LLM 请求失败: {exc}"}]
                continue

            candidates = _parse_json_array(content)
            last_round_results = []
            round_accepted = 0
            round_dup_rejected = 0
            budget_hit = False
            for item in candidates:
                rpn, hypothesis = self._validate_candidate(item)
                if rpn is None:
                    last_round_results.append({"rpn": item.get("rpn"), "result": "公式非法"})
                    rejected.append({"rpn": item.get("rpn"), "reason": "公式非法"})
                    continue
                rpn_key = tuple(rpn)
                if rpn_key in rejected_rpn:
                    # 前轮已评估且被拒绝的公式，直接判重复，不再跑 VM/打分
                    last_round_results.append({"rpn": rpn, "result": "与已评估候选重复"})
                    rejected.append({"rpn": rpn, "reason": "重复"})
                    round_dup_rejected += 1
                    continue
                if evaluated >= max_candidates:
                    budget_hit = True
                    break
                evaluated += 1
                try:
                    lookback = max_lookback_from_rpn(rpn)
                except ValueError as exc:
                    last_round_results.append({"rpn": rpn, "result": "公式非法"})
                    rejected.append({"rpn": rpn, "reason": "公式非法", "warning": str(exc)})
                    rejected_rpn.add(rpn_key)
                    continue
                available_discovery_warmup = discovery_eval_start - split.warmup_start
                if available_discovery_warmup < lookback - 1 or split.final_oos_warmup_days < lookback - 1:
                    reason = {
                        "reason": "INSUFFICIENT_LOOKBACK_HISTORY",
                        "required_lookback": lookback,
                        "available_discovery_warmup": available_discovery_warmup,
                        "available_final_oos_warmup": split.final_oos_warmup_days,
                    }
                    last_round_results.append({"rpn": rpn, "result": "历史预热不足", **reason})
                    rejected.append({"rpn": rpn, **reason})
                    rejected_rpn.add(rpn_key)
                    continue
                full_values = self.vm.execute(rpn, panel)
                if full_values is None:
                    last_round_results.append({"rpn": rpn, "result": "计算失败"})
                    rejected.append({"rpn": rpn, "reason": "计算失败"})
                    rejected_rpn.add(rpn_key)
                    continue
                discovery_values = full_values[:, discovery_eval_start:discovery_eval_end]
                if is_duplicate(rpn, discovery_values, library, active_panels):
                    last_round_results.append({"rpn": rpn, "result": "与库内因子重复"})
                    rejected.append({"rpn": rpn, "reason": "重复"})
                    rejected_rpn.add(rpn_key)
                    round_dup_rejected += 1
                    continue
                metrics = fitness_mod.evaluate_factor_range(
                    full_values,
                    closes,
                    eval_start=discovery_eval_start,
                    eval_end=discovery_eval_end,
                    horizon=horizon,
                )
                metrics = _with_neutralized_metrics(metrics)
                # 收紧后的门槛只会比基础门槛更严，可直接叠加在 passed 之上
                passed = bool(metrics.get("passed")) and metrics["rank_ic"] >= _rank_ic_threshold(
                    evaluated, research_config.evaluation.min_rank_ic
                )
                if not passed:
                    last_round_results.append({"rpn": rpn, "metrics": _prompt_safe_metrics(metrics), "result": "discovery 未达入库门槛"})
                    rejected.append({"rpn": rpn, "reason": "未达门槛", "metrics": metrics})
                    rejected_rpn.add(rpn_key)
                    continue
                candidate = DiscoveryCandidate(
                    rpn=rpn,
                    hypothesis=hypothesis,
                    discovery_metrics=metrics,
                    discovery_values=discovery_values,
                    full_values=full_values,
                    candidate_hash=_candidate_hash(rpn),
                    round_index=round_index,
                    evaluated_index=evaluated,
                    lookback=lookback,
                )
                discovery_candidates.append(candidate)
                active_panels[f"CANDIDATE:{candidate.candidate_hash}"] = discovery_values
                round_accepted += 1
                last_round_results.append({"rpn": rpn, "metrics": _prompt_safe_metrics(metrics), "result": "discovery 通过，进入待验证池"})

            if budget_hit:
                stopped_early = True
                stop_reason = f"达到候选评估预算上限 {max_candidates}，提前终止"
                break
            dup_rate = round_dup_rejected / len(candidates) if candidates else 0.0
            if round_accepted == 0 and dup_rate > _EARLY_STOP_DUP_RATE:
                consecutive_empty += 1
            else:
                consecutive_empty = 0
            if consecutive_empty >= _EARLY_STOP_ROUNDS:
                stopped_early = True
                stop_reason = (
                    f"连续 {_EARLY_STOP_ROUNDS} 轮无入库且判重率 > {_EARLY_STOP_DUP_RATE}，"
                    "判定搜索空间饱和，提前终止"
                )
                break

        accepted, oos_rejected, oos_diagnostics = self._run_final_oos_gate(
            discovery_candidates,
            library,
            panel,
            closes,
            split,
            horizon,
            symbols,
            model_name,
            dates=dates,
            eval_window=eval_window,
            lease_guard=lease_guard,
            data_version=data_version,
            data_snapshot_id=data_snapshot_id,
        )
        rejected.extend(oos_rejected)
        diagnostics.update(oos_diagnostics)
        return {
            "accepted": accepted,
            "rejected": rejected,
            "warning": None,
            "stopped_early": stopped_early,
            "stop_reason": stop_reason,
            "evaluated": evaluated,
            "diagnostics": diagnostics,
        }

    def _run_final_oos_gate(
        self,
        candidates: list[DiscoveryCandidate],
        library: dict,
        panel: dict[str, np.ndarray],
        closes: np.ndarray,
        split: FactorResearchSplit,
        horizon: int,
        symbols: list[str],
        model_name: str,
        dates: list[str] | None = None,
        eval_window: int | None = None,
        lease_guard: Callable[[], None] | None = None,
        data_version: str | None = None,
        data_snapshot_id: str | None = None,
    ) -> tuple[list[dict], list[dict], dict]:
        _ = panel
        accepted: list[dict] = []
        rejected: list[dict] = []
        diagnostics = {
            "oos_window_count": 0,
            "run_valid": True,
            "run_failure_code": None,
            "discovery_candidate_count": len(candidates),
        }
        research_run_id = f"factor-oos-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        for candidate in candidates:
            if lease_guard:
                lease_guard()
            oos = evaluate_oos_splits(
                candidate.full_values,
                closes,
                eval_start=split.final_oos_start,
                eval_end=split.final_oos_end,
                horizon=horizon,
            )
            window_count = len(oos.get("windows") or [])
            diagnostics["oos_window_count"] = max(diagnostics["oos_window_count"], window_count)
            if not window_count:
                audit_ref = self._write_oos_audit(
                    candidate=candidate,
                    oos={"passed": False, "warning": "FINAL_OOS_WINDOW_UNAVAILABLE"},
                    split=split,
                    horizon=horizon,
                    symbols=symbols,
                    dates=dates,
                    research_run_id=research_run_id,
                    data_version=data_version,
                    data_snapshot_id=data_snapshot_id,
                )
                diagnostics["run_valid"] = False
                diagnostics["run_failure_code"] = "FINAL_OOS_WINDOW_UNAVAILABLE"
                rejected.append({
                    "rpn": candidate.rpn,
                    "reason": "OOS窗口不可用",
                    "candidate_hash": candidate.candidate_hash,
                    "metrics": {
                        **candidate.discovery_metrics,
                        "passed": False,
                        "final_oos_audit_ref": audit_ref,
                    },
                })
                continue
            if not oos.get("passed"):
                audit_ref = self._write_oos_audit(
                    candidate=candidate,
                    oos=oos,
                    split=split,
                    horizon=horizon,
                    symbols=symbols,
                    dates=dates,
                    research_run_id=research_run_id,
                    data_version=data_version,
                    data_snapshot_id=data_snapshot_id,
                )
                rejected.append({
                    "rpn": candidate.rpn,
                    "reason": "OOS未通过",
                    "candidate_hash": candidate.candidate_hash,
                    "metrics": {
                        **candidate.discovery_metrics,
                        "passed": False,
                        "final_oos_audit_ref": audit_ref,
                    },
                })
                continue
            if lease_guard:
                lease_guard()
            audit_ref = self._write_oos_audit(
                candidate=candidate,
                oos=oos,
                split=split,
                horizon=horizon,
                symbols=symbols,
                dates=dates,
                research_run_id=research_run_id,
                data_version=data_version,
                data_snapshot_id=data_snapshot_id,
            )
            stored_metrics = dict(candidate.discovery_metrics)
            stored_metrics["final_oos_summary"] = {
                "method": oos.get("method"),
                "passed": True,
                "window_count": window_count,
                "mean_rank_ic": oos.get("mean_rank_ic"),
                "min_rank_ic": oos.get("min_rank_ic"),
                "window_pass_ratio": oos.get("window_pass_ratio"),
                "positive_window_ratio": oos.get("positive_window_ratio"),
                "oos_excess_return": oos.get("oos_excess_return"),
                "withheld": True,
            }
            stored_metrics["final_oos_audit_ref"] = audit_ref
            entry = add_factor(
                library,
                candidate.rpn,
                expression=" ".join(candidate.rpn),
                hypothesis=candidate.hypothesis,
                metrics=stored_metrics,
                universe=sorted(symbols) if len(symbols) <= 100 else [],
                horizon=horizon,
                llm_model=model_name,
            )
            entry["universe_size"] = len(symbols)
            entry["candidate_hash"] = candidate.candidate_hash
            entry["discovery_round"] = candidate.round_index
            entry["evaluated_index"] = candidate.evaluated_index
            entry["lookback"] = candidate.lookback
            if eval_window:
                entry["eval_window"] = eval_window
            accepted.append(entry)
        if accepted:
            if lease_guard:
                lease_guard()
            merge_result = save_library(library, self.library_path, lease_guard=lease_guard)
            by_hash = merge_result.persisted_by_hash
            accepted = [by_hash.get(str(item.get("candidate_hash"))) or item for item in accepted]
            # 最终 Library ID 在保存时才确定：追加 ID 分配事件而不是回写原审计行。
            for item in accepted:
                append_oos_audit(
                    {
                        "event": "FACTOR_ID_ASSIGNED",
                        "research_run_id": research_run_id,
                        "candidate_hash": item.get("candidate_hash"),
                        "factor_id": item.get("id"),
                        "data_version": data_version,
                        "data_snapshot_id": data_snapshot_id,
                    }
                )
        return accepted, rejected, diagnostics

    def _write_oos_audit(
        self,
        *,
        candidate: DiscoveryCandidate,
        oos: dict,
        split: FactorResearchSplit,
        horizon: int,
        symbols: list[str],
        dates: list[str] | None,
        research_run_id: str,
        data_version: str | None = None,
        data_snapshot_id: str | None = None,
    ) -> str:
        result = append_oos_audit(
            {
                "event": "FINAL_OOS_EVALUATED",
                "factor_id": None,
                "research_run_id": research_run_id,
                "candidate_hash": candidate.candidate_hash,
                "rpn": candidate.rpn,
                "hypothesis": candidate.hypothesis,
                "discovery_metrics": candidate.discovery_metrics,
                "final_oos": oos,
                "split": split.diagnostics(horizon, candidate.full_values.shape[1]),
                "date_ranges": _date_ranges(split, horizon, dates),
                "universe_size": len(symbols),
                "universe_hash": _universe_hash(symbols),
                "data_version": data_version,
                "data_snapshot_id": data_snapshot_id,
                "code_commit": os.getenv("CODE_COMMIT", "UNKNOWN"),
            }
        )
        return result.uri


__all__ = ["FactorMiner"]


def _with_neutralized_metrics(metrics: dict) -> dict:
    """Keep raw metrics explicit; neutralized metrics require real exposure data."""
    out = dict(metrics)
    out.setdefault("raw_rank_ic", out.get("rank_ic"))
    out.setdefault("raw_topk_excess_annual_return", out.get("topk_excess_annual_return"))
    out["neutralization_status"] = "NOT_AVAILABLE"
    out["neutralized_rank_ic"] = None
    out["neutralized_topk_excess_annual_return"] = None
    out["neutralized_topk_excess_return"] = None
    return out


def _prompt_safe_metrics(metrics: dict) -> dict:
    """Expose discovery metrics only; final OOS audit stays out of prompts."""
    blocked = {
        "oos",
        "final_oos",
        "final_oos_audit",
        "final_oos_summary",
        "final_oos_audit_ref",
        "windows",
        "mean_rank_ic",
        "min_rank_ic",
        "window_pass_ratio",
        "positive_window_ratio",
        "oos_excess_return",
    }
    return {key: value for key, value in (metrics or {}).items() if key not in blocked}


def _candidate_hash(rpn: list[str]) -> str:
    return sha256(json.dumps(list(rpn), ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _universe_hash(symbols: list[str]) -> str:
    return sha256(
        json.dumps(sorted(symbols), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _date_ranges(split: FactorResearchSplit, horizon: int, dates: list[str] | None) -> dict:
    if not dates:
        return {}

    def rng(start: int, end: int) -> tuple[str | None, str | None]:
        if end <= start or start >= len(dates):
            return (None, None)
        return (str(dates[start]), str(dates[min(end - 1, len(dates) - 1)]))

    return {
        "history": rng(split.warmup_start, split.discovery_start),
        "discovery": rng(split.discovery_start, split.discovery_end),
        "final_oos": rng(split.final_oos_start, split.final_oos_end),
        "future_return_observation": rng(split.final_oos_end, min(len(dates), split.final_oos_end + horizon)),
    }

"""LLM 驱动的自动因子挖掘。

流程：组装 DSL 说明与已有因子表现 → 请求 LLM 生成候选 RPN 公式 →
虚拟机校验可计算性 → 适应度打分 → 达标且非重复者入库 → 结果反馈进下一轮 prompt。
"""
from __future__ import annotations

import json
import logging
import os
import re

import numpy as np
import yaml

from engines.factor import fitness as fitness_mod
from engines.factor.library import (
    active_factors,
    add_factor,
    is_duplicate,
    load_library,
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
from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

_DEFAULT_ROUNDS = 3
_DEFAULT_CANDIDATES = 8
_DEFAULT_HORIZON = 5
_DEFAULT_MAX_CANDIDATES = 40  # 单次挖掘评估候选总数预算（env FACTOR_MINING_MAX_CANDIDATES）

# 饱和早停：连续 2 轮无入库且判重拒绝率 > 0.5 时判定搜索空间饱和
_EARLY_STOP_ROUNDS = 2
_EARLY_STOP_DUP_RATE = 0.5

# 多重检验收紧：累计评估候选数超过 30 后 rank_ic 门槛 ×1.5（简化 Bonferroni 校正，
# 只在此处生效，不修改 fitness.py 的基础阈值常量）
_BONFERRONI_EVAL_COUNT = 30
_BONFERRONI_FACTOR = 1.5


def _rank_ic_threshold(evaluated: int) -> float:
    """按累计评估候选数返回当前 rank_ic 入库门槛。"""
    if evaluated > _BONFERRONI_EVAL_COUNT:
        return fitness_mod.RANK_IC_THRESHOLD * _BONFERRONI_FACTOR
    return fitness_mod.RANK_IC_THRESHOLD

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
    ) -> str:
        ts_tokens = [f"{op}_{w}" for op in TS_OPS for w in TS_WINDOWS]
        ts_binary_tokens = [f"{op}_{w}" for op in TS_BINARY_OPS for w in TS_WINDOWS]
        seed_examples = [{"hypothesis": s["hypothesis"], "rpn": s["rpn"]} for s in _load_seed_entries()]
        top_factors = active_factors(library, limit=5)
        top_desc = (
            json.dumps([
                {"rpn": f["rpn"], "hypothesis": f.get("hypothesis", ""), "metrics": f.get("metrics", {})}
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
入库门槛: rank_ic >= {fitness_mod.RANK_IC_THRESHOLD} 且 icir >= {fitness_mod.ICIR_THRESHOLD}。
综合 fitness = 5*rank_ic + 0.5*icir + topk_annual_return。

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
    ) -> dict:
        """执行挖掘，返回 {accepted, rejected, warning, stopped_early, stop_reason, evaluated} 摘要。"""
        rounds = rounds or int(os.getenv("FACTOR_MINING_ROUNDS", _DEFAULT_ROUNDS))
        candidates_per_round = candidates_per_round or int(
            os.getenv("FACTOR_MINING_CANDIDATES_PER_ROUND", _DEFAULT_CANDIDATES))
        horizon = horizon or int(os.getenv("FACTOR_MINING_HORIZON_DAYS", _DEFAULT_HORIZON))
        max_candidates = int(os.getenv("FACTOR_MINING_MAX_CANDIDATES", _DEFAULT_MAX_CANDIDATES))

        if not self.model_client or not self.model_client.available():
            return {"accepted": [], "rejected": [], "warning": "挖掘模型不可用，请配置 ANALYSIS_MODEL_*",
                    "stopped_early": False, "stop_reason": None, "evaluated": 0}
        closes = panel.get("close")
        if closes is None or not symbols:
            return {"accepted": [], "rejected": [], "warning": "特征面板为空，无法挖掘",
                    "stopped_early": False, "stop_reason": None, "evaluated": 0}

        library = load_library(self.library_path)
        accepted: list[dict] = []
        rejected: list[dict] = []
        last_round_results: list[dict] | None = None
        model_name = getattr(self.model_client, "model", "") or ""
        evaluated = 0                       # 已跑 VM/打分的候选总数（预算与门槛收紧的计数基准）
        rejected_rpn: set[tuple[str, ...]] = set()  # 评估过但被拒绝的公式缓存，后续轮次直接判重复
        consecutive_empty = 0               # 连续「无入库且高重复率」的轮数
        stopped_early = False
        stop_reason: str | None = None

        # 预计算库内 active 因子面板用于相关性判重
        active_panels: dict[str, np.ndarray] = {}
        for factor in active_factors(library):
            panel_values = self.vm.execute(factor.get("rpn") or [], panel)
            if panel_values is not None:
                active_panels[factor["id"]] = panel_values
        # Alpha191 种子面板进判重池：引导 LLM 在种子思路上变异而非照抄
        for seed in _load_seed_entries():
            panel_values = self.vm.execute(seed["rpn"], panel)
            if panel_values is not None:
                active_panels[f"SEED:{seed['name'] or seed['hypothesis'][:12]}"] = panel_values

        for round_index in range(1, rounds + 1):
            prompt = self._build_prompt(library, candidates_per_round, horizon, round_index, last_round_results)
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
                panel_values = self.vm.execute(rpn, panel)
                if panel_values is None:
                    last_round_results.append({"rpn": rpn, "result": "计算失败"})
                    rejected.append({"rpn": rpn, "reason": "计算失败"})
                    rejected_rpn.add(rpn_key)
                    continue
                if is_duplicate(rpn, panel_values, library, active_panels):
                    last_round_results.append({"rpn": rpn, "result": "与库内因子重复"})
                    rejected.append({"rpn": rpn, "reason": "重复"})
                    rejected_rpn.add(rpn_key)
                    round_dup_rejected += 1
                    continue
                metrics = fitness_mod.evaluate_factor(panel_values, closes, horizon=horizon, eval_window=eval_window)
                # 收紧后的门槛只会比基础门槛更严，可直接叠加在 passed 之上
                passed = bool(metrics.get("passed")) and metrics["rank_ic"] >= _rank_ic_threshold(evaluated)
                if not passed:
                    last_round_results.append({"rpn": rpn, "metrics": metrics, "result": "未达入库门槛"})
                    rejected.append({"rpn": rpn, "reason": "未达门槛", "metrics": metrics})
                    rejected_rpn.add(rpn_key)
                    continue
                entry = add_factor(
                    library, rpn, expression=" ".join(rpn), hypothesis=hypothesis,
                    metrics=metrics, universe=sorted(symbols) if len(symbols) <= 100 else [],
                    horizon=horizon, llm_model=model_name,
                )
                entry["universe_size"] = len(symbols)
                if eval_window:
                    entry["eval_window"] = eval_window
                active_panels[entry["id"]] = panel_values
                accepted.append(entry)
                round_accepted += 1
                last_round_results.append({"rpn": rpn, "metrics": metrics, "result": "已入库"})

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

        save_library(library, self.library_path)
        return {
            "accepted": accepted,
            "rejected": rejected,
            "warning": None,
            "stopped_early": stopped_early,
            "stop_reason": stop_reason,
            "evaluated": evaluated,
        }


__all__ = ["FactorMiner"]

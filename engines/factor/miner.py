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
    TS_OPS,
    TS_WINDOWS,
    UNARY_OPS,
    is_valid_token,
)
from engines.factor.vm import StackVM

logger = logging.getLogger(__name__)

_DEFAULT_ROUNDS = 3
_DEFAULT_CANDIDATES = 8
_DEFAULT_HORIZON = 5

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
  （面板形状为 标的×交易日；ret 为日收益，vwap=amount/volume）
- 时序算子(沿时间轴): {ts_tokens}
- 横截面算子(逐日截面): {list(CS_OPS)}
- 一元算子: {list(UNARY_OPS)}
- 二元算子: {list(BINARY_OPS)}
- 规则: 公式总长度 ≤ {MAX_FORMULA_TOKENS} 个 token；必须以 cs_rank/cs_zscore/cs_demean 之一收尾以保证截面可比；
  不能用数值常量，窗口已烘焙在算子名中。
  二元算子需要两个操作数：若两个操作数都源自同一特征，需把该特征名连续压栈两次（见示例 5日反转）。

## 经典示例
{json.dumps(_EXAMPLES, ensure_ascii=False, indent=1)}

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
        """执行挖掘，返回 {accepted, rejected, warning} 摘要。"""
        rounds = rounds or int(os.getenv("FACTOR_MINING_ROUNDS", _DEFAULT_ROUNDS))
        candidates_per_round = candidates_per_round or int(
            os.getenv("FACTOR_MINING_CANDIDATES_PER_ROUND", _DEFAULT_CANDIDATES))
        horizon = horizon or int(os.getenv("FACTOR_MINING_HORIZON_DAYS", _DEFAULT_HORIZON))

        if not self.model_client or not self.model_client.available():
            return {"accepted": [], "rejected": [], "warning": "挖掘模型不可用，请配置 ANALYSIS_MODEL_*"}
        closes = panel.get("close")
        if closes is None or not symbols:
            return {"accepted": [], "rejected": [], "warning": "特征面板为空，无法挖掘"}

        library = load_library(self.library_path)
        accepted: list[dict] = []
        rejected: list[dict] = []
        last_round_results: list[dict] | None = None
        model_name = getattr(self.model_client, "model", "") or ""

        # 预计算库内 active 因子面板用于相关性判重
        active_panels: dict[str, np.ndarray] = {}
        for factor in active_factors(library):
            panel_values = self.vm.execute(factor.get("rpn") or [], panel)
            if panel_values is not None:
                active_panels[factor["id"]] = panel_values

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
            for item in candidates:
                rpn, hypothesis = self._validate_candidate(item)
                if rpn is None:
                    last_round_results.append({"rpn": item.get("rpn"), "result": "公式非法"})
                    rejected.append({"rpn": item.get("rpn"), "reason": "公式非法"})
                    continue
                panel_values = self.vm.execute(rpn, panel)
                if panel_values is None:
                    last_round_results.append({"rpn": rpn, "result": "计算失败"})
                    rejected.append({"rpn": rpn, "reason": "计算失败"})
                    continue
                if is_duplicate(rpn, panel_values, library, active_panels):
                    last_round_results.append({"rpn": rpn, "result": "与库内因子重复"})
                    rejected.append({"rpn": rpn, "reason": "重复"})
                    continue
                metrics = fitness_mod.evaluate_factor(panel_values, closes, horizon=horizon, eval_window=eval_window)
                if not metrics.get("passed"):
                    last_round_results.append({"rpn": rpn, "metrics": metrics, "result": "未达入库门槛"})
                    rejected.append({"rpn": rpn, "reason": "未达门槛", "metrics": metrics})
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
                last_round_results.append({"rpn": rpn, "metrics": metrics, "result": "已入库"})

        save_library(library, self.library_path)
        return {"accepted": accepted, "rejected": rejected, "warning": None}


__all__ = ["FactorMiner"]

"""决策归因（Decision Evaluation v2）：把一次结果评估分解为可机器消费的对错维度。

`build_attribution(decision, outcome) -> dict` 纯确定性规则，不依赖 LLM。输出：

    {
        "decision_id": str | None,
        "horizon": int | None,
        "correct": [token...],    # 判断正确的维度
        "wrong": [token...],      # 判断错误的维度
        "unknown": [token...],    # 数据不足/中性的维度
        "contribution": {         # 收益贡献分解（收益空间，可 None）
            "market_regime": float | None,   # 市场β：market_return
            "theme_selection": float | None, # 主题/行业选择：行业相对市场超额（sector_excess），
                                             # 无行业腿时回退 theme_excess
            "stock_selection": float | None, # 标的选择：组合相对行业（或市场）基准的超额
            "timing": float | None,          # 时点：持有期最大不利偏移 MAE（≤0，越深入场越差）
        },
    }

稳定机器 token（供复盘区分 市场判断/方向判断/标的选择/时点）：
    market_regime / direction / stock_selection / entry_timing / sector_selection / theme_selection

判定规则（SIGNIFICANT = 2% 显著性阈值）：
1. direction（方向判断）：absolute_return > +2% → correct；< -2% → wrong；否则 unknown。
2. market_regime（市场判断）：market_return 缺失 → unknown；
   market_return > +2% 且 absolute_return > 0 → correct（顺势做多）；
   market_return < -2% 且 absolute_return < 0 → wrong（下跌市中持有多头）；否则 unknown。
3. stock_selection（标的选择）：market_excess_return > +2% → correct；< -2% → wrong；
   缺失或中性 → unknown。
4. sector_selection（行业选择）：market_excess > +2% 且 sector_excess < -2% → wrong
   （跑赢市场却跑输行业，说明选错行业、靠选股补救）；sector_excess > +2% → correct；
   缺失或中性 → unknown。
5. theme_selection（主题选择）：theme_excess_return > +2% → correct；< -2% → wrong；
   缺失或中性 → unknown（当前等权口径下 theme_excess ≈ 0，通常为 unknown）。
6. entry_timing（入场时点）：MAE ≤ -5% 且期末 absolute_return 较 MAE 回升 ≥ 3%
   → wrong（深亏后修复，入场时点不佳）；MAE ≥ -1% → correct（持有期几乎无不利偏移）；
   否则 unknown。
"""
from __future__ import annotations

from typing import Any

SIGNIFICANT = 0.02
TIMING_MAE_DEEP = -0.05
TIMING_RECOVERY = 0.03
TIMING_MAE_CALM = -0.01

TOKEN_LABELS = {
    "market_regime": "市场判断",
    "direction": "方向判断",
    "stock_selection": "标的选择",
    "entry_timing": "入场时点",
    "sector_selection": "行业选择",
    "theme_selection": "主题选择",
}


def build_attribution(decision: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    absolute = _num(outcome.get("absolute_return", outcome.get("portfolio_return")))
    market = _num(outcome.get("market_return", outcome.get("benchmark_return")))
    market_excess = _num(outcome.get("market_excess_return", outcome.get("excess_return")))
    sector_excess = _num(outcome.get("sector_excess_return"))
    sector_return = _num(outcome.get("sector_return"))
    theme_excess = _num(outcome.get("theme_excess_return"))
    mae = _num(outcome.get("max_adverse_excursion"))

    correct: list[str] = []
    wrong: list[str] = []
    unknown: list[str] = []

    def classify(token: str, value: float | None) -> None:
        if value is None or abs(value) <= SIGNIFICANT:
            unknown.append(token)
        elif value > 0:
            correct.append(token)
        else:
            wrong.append(token)

    classify("direction", absolute)

    if market is None or absolute is None:
        unknown.append("market_regime")
    elif market > SIGNIFICANT and absolute > 0:
        correct.append("market_regime")
    elif market < -SIGNIFICANT and absolute < 0:
        wrong.append("market_regime")
    else:
        unknown.append("market_regime")

    classify("stock_selection", market_excess)

    if sector_excess is None:
        unknown.append("sector_selection")
    elif market_excess is not None and market_excess > SIGNIFICANT and sector_excess < -SIGNIFICANT:
        wrong.append("sector_selection")
    else:
        classify("sector_selection", sector_excess)

    classify("theme_selection", theme_excess)

    if mae is None:
        unknown.append("entry_timing")
    elif mae <= TIMING_MAE_DEEP and absolute is not None and absolute - mae >= TIMING_RECOVERY:
        wrong.append("entry_timing")
    elif mae >= TIMING_MAE_CALM:
        correct.append("entry_timing")
    else:
        unknown.append("entry_timing")

    stock_base = sector_return if sector_return is not None else market
    contribution = {
        "market_regime": market,
        "theme_selection": sector_excess if sector_excess is not None else theme_excess,
        "stock_selection": (absolute - stock_base) if absolute is not None and stock_base is not None else None,
        "timing": mae,
    }
    return {
        "decision_id": decision.get("id") or decision.get("decision_id"),
        "horizon": outcome.get("horizon_days"),
        "correct": correct,
        "wrong": wrong,
        "unknown": unknown,
        "contribution": contribution,
    }


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None

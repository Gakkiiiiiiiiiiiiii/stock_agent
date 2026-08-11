"""机会候选硬过滤（eligible / reject），纯函数、确定性。

拒因使用稳定机器码：
QUOTE_MISSING / SUSPENDED / LIQUIDITY_TOO_LOW / DATA_COVERAGE_LOW /
THEME_INVALIDATED / TECHNICAL_INVALIDATED / RISK_LIMIT_EXCEEDED
"""
from __future__ import annotations

from engines.opportunity.candidate import OpportunityCandidate

QUOTE_MISSING = "QUOTE_MISSING"
SUSPENDED = "SUSPENDED"
LIQUIDITY_TOO_LOW = "LIQUIDITY_TOO_LOW"
DATA_COVERAGE_LOW = "DATA_COVERAGE_LOW"
THEME_INVALIDATED = "THEME_INVALIDATED"
TECHNICAL_INVALIDATED = "TECHNICAL_INVALIDATED"
RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"

#: 默认门槛；service / pipeline 从 config/opportunity.yaml 注入。
DEFAULT_MIN_LIQUIDITY_SCORE = 20.0
DEFAULT_MIN_DATA_COVERAGE = 0.60


def evaluate_eligibility(
    candidate: OpportunityCandidate,
    symbol_context: dict | None = None,
    *,
    min_liquidity_score: float = DEFAULT_MIN_LIQUIDITY_SCORE,
    min_data_coverage: float = DEFAULT_MIN_DATA_COVERAGE,
) -> dict:
    """对单个候选做硬过滤。

    symbol_context 键（均可选，缺失按通过处理）：
      - quote_available: bool，False → QUOTE_MISSING
      - suspended: bool，True → SUSPENDED
      - data_coverage: float 0-1，低于 min_data_coverage → DATA_COVERAGE_LOW
      - theme_invalidated: bool，True → THEME_INVALIDATED
      - technical_invalidated: bool，True → TECHNICAL_INVALIDATED
      - requested_weight / risk_cap: float，请求权重超过个股风险上限 → RISK_LIMIT_EXCEEDED
    candidate.liquidity_score 低于 min_liquidity_score → LIQUIDITY_TOO_LOW（None 跳过该检查）。
    """
    ctx = symbol_context or {}
    reasons: list[str] = []
    if ctx.get("quote_available") is False:
        reasons.append(QUOTE_MISSING)
    if ctx.get("suspended") is True:
        reasons.append(SUSPENDED)
    if candidate.liquidity_score is not None and candidate.liquidity_score < min_liquidity_score:
        reasons.append(LIQUIDITY_TOO_LOW)
    coverage = ctx.get("data_coverage")
    if coverage is not None and float(coverage) < min_data_coverage:
        reasons.append(DATA_COVERAGE_LOW)
    if ctx.get("theme_invalidated") is True:
        reasons.append(THEME_INVALIDATED)
    if ctx.get("technical_invalidated") is True:
        reasons.append(TECHNICAL_INVALIDATED)
    requested_weight = ctx.get("requested_weight")
    risk_cap = ctx.get("risk_cap")
    if requested_weight is not None and risk_cap is not None and float(requested_weight) > float(risk_cap):
        reasons.append(RISK_LIMIT_EXCEEDED)
    return {"symbol": candidate.symbol, "eligible": not reasons, "reject_reasons": reasons}

"""机会评分：OpportunityScore = Σ w_i * component_i − w_risk * RiskPenalty。

权重来自 config/opportunity.yaml；缺失成分按中性分（默认 50）处理并记录 note。
纯函数、确定性，无 LLM。
"""
from __future__ import annotations

from financial_agent.config import load_yaml_config
from engines.opportunity.candidate import COMPONENT_ORDER, OpportunityCandidate

OPPORTUNITY_CONFIG_FILE = "opportunity.yaml"
DEFAULT_VERSION = "opportunity_ranking_v1"
DEFAULT_NEUTRAL_SCORE = 50.0
DEFAULT_WEIGHTS: dict[str, float] = {
    "theme": 0.25,
    "technical": 0.20,
    "alpha": 0.20,
    "regime_fit": 0.15,
    "knowledge": 0.10,
    "risk": 0.10,
}

#: 成分名 → OpportunityCandidate 字段名。
_COMPONENT_FIELDS: dict[str, str] = {
    "theme": "theme_score",
    "technical": "technical_score",
    "alpha": "factor_score",
    "regime_fit": "regime_fit_score",
    "knowledge": "knowledge_score",
    "risk": "risk_score",
}


def load_opportunity_config() -> dict:
    """读取 config/opportunity.yaml 的 opportunity 段；文件缺失时返回内置默认。"""
    try:
        data = load_yaml_config(OPPORTUNITY_CONFIG_FILE)
    except FileNotFoundError:
        data = {}
    section = dict(data.get("opportunity") or {})
    section.setdefault("version", DEFAULT_VERSION)
    section.setdefault("neutral_component_score", DEFAULT_NEUTRAL_SCORE)
    section.setdefault("weights", dict(DEFAULT_WEIGHTS))
    section.setdefault(
        "eligibility",
        {"min_liquidity_score": 20.0, "min_data_coverage": 0.60},
    )
    return section


def score_candidate(candidate: OpportunityCandidate, config: dict | None = None) -> dict:
    """计算单个候选的机会分。

    返回 {symbol, opportunity_score, components, notes}：
      - components: 固定顺序的六成分分（缺失成分已填中性分）
      - notes: 稳定机器码，MISSING_COMPONENT_<NAME> 表示该成分缺数据用了中性分
    """
    cfg = config or load_opportunity_config()
    weights = dict(cfg.get("weights") or DEFAULT_WEIGHTS)
    neutral = float(cfg.get("neutral_component_score", DEFAULT_NEUTRAL_SCORE))

    components: dict[str, float] = {}
    notes: list[str] = []
    for name in COMPONENT_ORDER:
        raw = getattr(candidate, _COMPONENT_FIELDS[name])
        if raw is None:
            components[name] = neutral
            notes.append(f"MISSING_COMPONENT_{name.upper()}")
        else:
            components[name] = round(float(raw), 4)

    score = 0.0
    for name in COMPONENT_ORDER:
        weight = float(weights.get(name, 0.0))
        if name == "risk":
            score -= weight * components[name]
        else:
            score += weight * components[name]
    score = max(0.0, min(100.0, score))
    return {
        "symbol": candidate.symbol,
        "opportunity_score": round(score, 4),
        "components": components,
        "notes": notes,
    }

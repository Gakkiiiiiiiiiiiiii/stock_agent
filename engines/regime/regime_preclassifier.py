"""市场状态预分类器。

保留旧版规则级联 ``preclassify_regime``（签名与置信度公式不变，供既有调用方/测试使用），
并新增概率向量分类器 ``classify_regime_probabilities``：

输入为 engines/market 下各子引擎产出的 [0,1] 子分数
（breadth / trend_score / crowding_score / rotation_score / range_score / drawdown_risk / retreat_score），
通过逐 regime 隶属函数（membership function）映射为隶属度，再归一化为
5 个 regime + UNKNOWN 的概率向量。全程确定性，阈值来自
config/market_regime_thresholds.yaml（market_regime 段复用旧阈值，
probability_model 段为新增参数）。

隶属函数定义（ramp_up/ramp_down 为线性过渡带，band 为软带隶属）：

- high_position_retreat:
    ramp_up(retreat_score, t - 0.25, t + 0.05)，t = min_retreat_score
- downtrend_market:
    0.6 * ramp_up(drawdown_risk, t_dd - 0.20, t_dd + 0.10)
    + 0.4 * ramp_down(breadth, t_b - 0.10, t_b + 0.15)
    t_dd = downtrend.min_drawdown_risk, t_b = downtrend.max_breadth
- crowding_market:
    ramp_up(crowding_score, c - 0.15, c + 0.10)，c = probability_model.crowding_center
- range_market:
    0.5 * band(breadth, range.min_breadth, range.max_breadth, soft=0.10)
    + 0.5 * range_score
- rotation_market:
    0.55 * rotation_score
    + 0.45 * ramp_up(breadth, t_r - 0.10, t_r + 0.15)，t_r = rotation.min_breadth

缺失特征处理：每个 regime 声明其依赖的子分数槽位，缺失槽位占比累积为
UNKNOWN 质量（unknown_mass = 缺失槽位 / 总槽位），并按可用性折减对应 regime
隶属度。归一化在 {5 regime 隶属度, unknown_mass} 上进行，概率和恒为 1。
confidence 取 5 个 regime 中的最高概率（不含 UNKNOWN），因此特征缺失越多，
UNKNOWN 质量越大，confidence 自然越低。
"""
from __future__ import annotations

from typing import Any

from financial_agent.config import load_yaml_config

UNKNOWN_REGIME = "UNKNOWN"

REGIMES = (
    "high_position_retreat",
    "downtrend_market",
    "crowding_market",
    "range_market",
    "rotation_market",
)

# 各 regime 隶属函数依赖的子分数槽位
_REGIME_REQUIRED_FEATURES: dict[str, tuple[str, ...]] = {
    "high_position_retreat": ("retreat_score",),
    "downtrend_market": ("drawdown_risk", "breadth"),
    "crowding_market": ("crowding_score",),
    "range_market": ("breadth", "range_score"),
    "rotation_market": ("rotation_score", "breadth"),
}

_DEFAULT_PROBABILITY_MODEL = {
    "crowding_center": 0.72,
    "base_membership": 0.02,
}


def _unknown(missing_fields: list[str]) -> dict:
    return {
        "primary_regime": UNKNOWN_REGIME,
        "secondary_regime": None,
        "confidence": 0.0,
        "missing_fields": missing_fields,
    }


def preclassify_regime(features: dict) -> dict:
    required = ("breadth", "crowding_score", "drawdown_risk", "retreat_score")
    missing = [name for name in required if features.get(name) is None]
    if missing:
        return _unknown(missing)
    thresholds = load_yaml_config("market_regime_thresholds.yaml")["market_regime"]
    retreat_score = features["retreat_score"]
    crowding = features["crowding_score"]
    breadth = features["breadth"]
    drawdown_risk = features["drawdown_risk"]
    if retreat_score >= thresholds["high_position_retreat"]["min_retreat_score"]:
        primary = "high_position_retreat"
    elif drawdown_risk >= thresholds["downtrend"]["min_drawdown_risk"] and breadth <= thresholds["downtrend"]["max_breadth"]:
        primary = "downtrend_market"
    elif crowding >= 0.72:
        primary = "crowding_market"
    elif thresholds["range"]["min_breadth"] <= breadth <= thresholds["range"]["max_breadth"]:
        primary = "range_market"
    else:
        primary = "rotation_market"
    return {
        "primary_regime": primary,
        "secondary_regime": "rotation_market" if primary != "rotation_market" else "range_market",
        "confidence": round(max(crowding, 1 - drawdown_risk, 0.55), 4),
    }


def _ramp_up(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _ramp_down(x: float, lo: float, hi: float) -> float:
    return 1.0 - _ramp_up(x, lo, hi)


def _band(x: float, lo: float, hi: float, soft: float) -> float:
    if lo <= x <= hi:
        return 1.0
    if x < lo:
        return _ramp_up(x, lo - soft, lo)
    return _ramp_down(x, hi, hi + soft)


def load_probability_thresholds() -> dict[str, Any]:
    """加载 market_regime 阈值段与 probability_model 参数段（缺失时回退默认值）。"""
    data = load_yaml_config("market_regime_thresholds.yaml")
    model = {**_DEFAULT_PROBABILITY_MODEL, **dict(data.get("probability_model") or {})}
    return {"market_regime": dict(data.get("market_regime") or {}), "probability_model": model}


def compute_regime_memberships(features: dict, thresholds: dict[str, Any] | None = None) -> tuple[dict[str, float], list[str]]:
    """计算 5 个 regime 的原始隶属度（含 base_membership 底噪，未归一化）。

    返回 (memberships, missing_fields)；缺失子分数对应的 regime 隶属度按槽位可用性折减。
    """
    if thresholds is None:
        thresholds = load_probability_thresholds()
    regime_cfg = dict(thresholds.get("market_regime") or {})
    model = dict(thresholds.get("probability_model") or _DEFAULT_PROBABILITY_MODEL)
    base = float(model.get("base_membership", 0.02))

    retreat_t = float(regime_cfg.get("high_position_retreat", {}).get("min_retreat_score", 0.65))
    down_cfg = dict(regime_cfg.get("downtrend") or {})
    down_dd_t = float(down_cfg.get("min_drawdown_risk", 0.6))
    down_breadth_t = float(down_cfg.get("max_breadth", 0.35))
    crowding_center = float(model.get("crowding_center", 0.72))
    range_cfg = dict(regime_cfg.get("range") or {})
    range_lo = float(range_cfg.get("min_breadth", 0.35))
    range_hi = float(range_cfg.get("max_breadth", 0.6))
    rotation_breadth_t = float(dict(regime_cfg.get("rotation") or {}).get("min_breadth", 0.45))

    def _value(name: str) -> float | None:
        value = features.get(name)
        return float(value) if value is not None else None

    raw: dict[str, float | None] = {}
    retreat = _value("retreat_score")
    raw["high_position_retreat"] = None if retreat is None else _ramp_up(retreat, retreat_t - 0.25, retreat_t + 0.05)

    drawdown = _value("drawdown_risk")
    breadth = _value("breadth")
    if drawdown is None or breadth is None:
        raw["downtrend_market"] = None if drawdown is None and breadth is None else (
            0.6 * _ramp_up(drawdown, down_dd_t - 0.20, down_dd_t + 0.10) if drawdown is not None
            else 0.4 * _ramp_down(breadth, down_breadth_t - 0.10, down_breadth_t + 0.15)
        )
    else:
        raw["downtrend_market"] = (
            0.6 * _ramp_up(drawdown, down_dd_t - 0.20, down_dd_t + 0.10)
            + 0.4 * _ramp_down(breadth, down_breadth_t - 0.10, down_breadth_t + 0.15)
        )

    crowding = _value("crowding_score")
    raw["crowding_market"] = None if crowding is None else _ramp_up(crowding, crowding_center - 0.15, crowding_center + 0.10)

    range_score = _value("range_score")
    if breadth is None and range_score is None:
        raw["range_market"] = None
    elif breadth is None:
        raw["range_market"] = 0.5 * range_score
    elif range_score is None:
        raw["range_market"] = 0.5 * _band(breadth, range_lo, range_hi, soft=0.10)
    else:
        raw["range_market"] = 0.5 * _band(breadth, range_lo, range_hi, soft=0.10) + 0.5 * range_score

    rotation = _value("rotation_score")
    if rotation is None and breadth is None:
        raw["rotation_market"] = None
    elif rotation is None:
        raw["rotation_market"] = 0.45 * _ramp_up(breadth, rotation_breadth_t - 0.10, rotation_breadth_t + 0.15)
    elif breadth is None:
        raw["rotation_market"] = 0.55 * rotation
    else:
        raw["rotation_market"] = 0.55 * rotation + 0.45 * _ramp_up(breadth, rotation_breadth_t - 0.10, rotation_breadth_t + 0.15)

    missing_fields = sorted({name for name in _REGIME_REQUIRED_FEATURES_VALUES() if features.get(name) is None})
    memberships = {regime: base + (raw[regime] if raw[regime] is not None else 0.0) for regime in REGIMES}
    return memberships, missing_fields


def _REGIME_REQUIRED_FEATURES_VALUES() -> tuple[str, ...]:
    names: list[str] = []
    for required in _REGIME_REQUIRED_FEATURES.values():
        for name in required:
            if name not in names:
                names.append(name)
    return tuple(names)


def classify_regime_probabilities(features: dict, thresholds: dict[str, Any] | None = None) -> dict:
    """将子分数特征映射为 5 regime + UNKNOWN 的归一化概率向量（完全确定性）。

    返回:
        probabilities: 5 个 regime 的概率（和 + unknown_probability == 1）
        unknown_probability: 缺失特征折算的 UNKNOWN 质量
        primary_regime: 概率最高的 regime；UNKNOWN 质量超过任一 regime 时为 UNKNOWN
        confidence: 5 个 regime 中的最高概率（不含 UNKNOWN）
        memberships: 归一化前的原始隶属度（含底噪）
        missing_fields: 缺失的子分数名
    """
    memberships, missing_fields = compute_regime_memberships(features, thresholds)
    total_slots = sum(len(required) for required in _REGIME_REQUIRED_FEATURES.values())
    missing_slots = sum(
        sum(1 for name in required if features.get(name) is None)
        for required in _REGIME_REQUIRED_FEATURES.values()
    )
    unknown_mass = missing_slots / total_slots if total_slots else 1.0

    total = sum(memberships.values()) + unknown_mass
    if total <= 0:
        probabilities = {regime: 0.0 for regime in REGIMES}
        unknown_probability = 1.0
    else:
        probabilities = {regime: round(memberships[regime] / total, 4) for regime in REGIMES}
        unknown_probability = round(unknown_mass / total, 4)

    primary = max(REGIMES, key=lambda regime: probabilities[regime])
    top_probability = probabilities[primary]
    if unknown_probability > top_probability:
        primary = UNKNOWN_REGIME
        top_probability = 0.0
    return {
        "probabilities": probabilities,
        "unknown_probability": unknown_probability,
        "primary_regime": primary,
        "confidence": round(top_probability, 4),
        "memberships": {regime: round(value, 4) for regime, value in memberships.items()},
        "missing_fields": missing_fields,
    }


def blend_probabilities(deterministic: dict[str, float], llm_hint: dict[str, float] | None, weight: float = 0.25) -> dict[str, float]:
    """按 ``final = (1-w)*deterministic + w*llm`` 融合 LLM 概率提示并重新归一化。

    llm_hint 为 None / 空 / 全缺失时原样返回 deterministic（确定性-only 路径）。
    """
    cleaned: dict[str, float] = {}
    for regime in REGIMES:
        value = (llm_hint or {}).get(regime)
        if value is not None:
            cleaned[regime] = max(0.0, float(value))
    if not cleaned:
        return {regime: round(float(deterministic.get(regime, 0.0)), 4) for regime in REGIMES}
    hint_total = sum(cleaned.values())
    if hint_total <= 0:
        return {regime: round(float(deterministic.get(regime, 0.0)), 4) for regime in REGIMES}
    weight = max(0.0, min(1.0, float(weight)))
    blended = {
        regime: (1 - weight) * float(deterministic.get(regime, 0.0)) + weight * (cleaned.get(regime, 0.0) / hint_total)
        for regime in REGIMES
    }
    total = sum(blended.values())
    if total <= 0:
        return {regime: round(1.0 / len(REGIMES), 4) for regime in REGIMES}
    return {regime: round(value / total, 4) for regime, value in blended.items()}

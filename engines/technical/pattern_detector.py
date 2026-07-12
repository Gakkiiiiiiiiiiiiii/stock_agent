from __future__ import annotations

from collections.abc import Sequence

from financial_agent.models import SignalResult


def _last(values: Sequence[float | None]) -> float | None:
    return values[-1] if values else None


def _cross_up(prev_a: float | None, prev_b: float | None, a: float | None, b: float | None) -> bool:
    return prev_a is not None and prev_b is not None and a is not None and b is not None and prev_a <= prev_b and a > b


def count_below(values: Sequence[float], baselines: Sequence[float | None], lookback: int) -> int:
    pairs = list(zip(values, baselines))[-lookback:]
    return sum(1 for value, baseline in pairs if baseline is not None and value < baseline)


def detect_b1(
    closes: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    indicators: dict[str, Sequence[float | None]],
    sector_strength: float = 50,
    rps_score: float | None = None,
) -> SignalResult:
    close = closes[-1]
    low = lows[-1]
    ltl_value = _last(indicators["ltl"])
    kdj_j = _last(indicators["kdj_j"])
    volume_ratio20 = _last(indicators["volume_ratio20"])
    evidence: list[str] = []
    risk: list[str] = []
    score = 0

    if ltl_value is not None and close >= ltl_value * 0.975:
        score += 24
        evidence.append("收盘价接近或站上 LTL")
    else:
        risk.append("收盘价距离 LTL 偏弱或 LTL 数据不足")
    if ltl_value is not None and low >= ltl_value * 0.94:
        score += 12
        evidence.append("低点未显著跌破 LTL")
    if count_below(closes, indicators["ltl"], 10) <= 4:
        score += 12
        evidence.append("近 10 日跌破 LTL 次数不多")
    if volume_ratio20 is not None and volume_ratio20 <= 1.10:
        score += 18
        evidence.append("回调量能未明显放大")
    else:
        risk.append("量能不够收敛")
    if kdj_j is not None and kdj_j < 30:
        score += 14
        evidence.append("KDJ J 值处于低位")
    if sector_strength >= 60:
        score += 10
        evidence.append("行业或主题强度不弱")
    else:
        risk.append("行业或主题强度一般")
    if rps_score is not None and rps_score >= 75:
        score += 10
        evidence.append("个股仍接近强势池")

    return SignalResult(
        pattern="B1",
        triggered=score >= 70,
        score=min(score, 100),
        entry_type="左侧试探",
        evidence=evidence,
        risk=risk,
        confirm_condition="重新放量站上短期均线",
        stop_condition="有效跌破 LTL 或平台下沿",
    )


def detect_b2(
    closes: Sequence[float],
    highs: Sequence[float],
    volumes: Sequence[float],
    indicators: dict[str, Sequence[float | None]],
    sector_strength: float = 50,
) -> SignalResult:
    close = closes[-1]
    prev_close = closes[-2] if len(closes) >= 2 else close
    daily_gain = close / prev_close - 1 if prev_close else 0
    lookback_high = max(highs[-21:-1]) if len(highs) > 21 else max(highs[:-1] or highs)
    volume_ratio20 = _last(indicators["volume_ratio20"])
    ma20 = _last(indicators["ma20"])
    evidence: list[str] = []
    risk: list[str] = []
    score = 0

    if daily_gain >= 0.04:
        score += 25
        evidence.append("出现 4% 以上中大阳线")
    if close >= lookback_high:
        score += 25
        evidence.append("收盘价突破近 20 日平台高点")
    if volume_ratio20 is not None and volume_ratio20 >= 1.35:
        score += 20
        evidence.append("成交量较 20 日均量明显放大")
    else:
        risk.append("突破量能确认不足")
    if ma20 is not None and close > ma20:
        score += 10
        evidence.append("股价站上 MA20")
    if sector_strength >= 60:
        score += 15
        evidence.append("行业或主题同步走强")
    else:
        risk.append("行业同步性不足")
    if len(closes) >= 60 and close / min(closes[-60:]) > 1.8:
        score -= 15
        risk.append("短期涨幅过大，需防高位末端加速")

    return SignalResult(
        pattern="B2",
        triggered=score >= 75,
        score=max(0, min(score, 100)),
        entry_type="右侧确认",
        evidence=evidence,
        risk=risk,
        confirm_condition="次日不跌回突破位且量能保持",
        stop_condition="缩量回落至突破位下方",
    )


def detect_b3(
    closes: Sequence[float],
    highs: Sequence[float],
    volumes: Sequence[float],
    indicators: dict[str, Sequence[float | None]],
    theme_strength: float = 50,
) -> SignalResult:
    close = closes[-1]
    ma10 = _last(indicators["ma10"])
    ma20 = _last(indicators["ma20"])
    volume_ratio20 = _last(indicators["volume_ratio20"])
    recent_high = max(highs[-20:])
    evidence: list[str] = []
    risk: list[str] = []
    score = 0

    if len(closes) >= 40 and close / closes[-40] > 1.15:
        score += 20
        evidence.append("前期已经完成趋势确认")
    if ma10 is not None and close >= ma10 * 0.98:
        score += 20
        evidence.append("短期回踩未有效跌破 MA10")
    if ma20 is not None and close >= ma20:
        score += 20
        evidence.append("仍在 MA20 上方运行")
    if volume_ratio20 is not None and volume_ratio20 <= 1.5:
        score += 15
        evidence.append("量能没有明显失控")
    else:
        risk.append("量能过热或数据不足")
    if theme_strength >= 65:
        score += 15
        evidence.append("主题仍在持续")
    if close / recent_high > 0.98 and volume_ratio20 is not None and volume_ratio20 > 1.8:
        score -= 15
        risk.append("接近高位且放量，补票性价比下降")

    return SignalResult(
        pattern="B3",
        triggered=score >= 70,
        score=max(0, min(score, 100)),
        entry_type="主升中继补票",
        evidence=evidence,
        risk=risk,
        confirm_condition="缩量回踩后重新转强",
        stop_condition="跌破 MA20 或主题强度快速下降",
    )


def detect_macd_triple_golden(indicators: dict[str, Sequence[float | None]]) -> SignalResult:
    ma5, ma10 = indicators["ma5"], indicators["ma10"]
    dif, dea = indicators["dif"], indicators["dea"]
    vma5, vma10 = indicators["volume_ma5"], indicators["volume_ma10"]
    if min(len(ma5), len(ma10), len(dif), len(dea), len(vma5), len(vma10)) < 2:
        return SignalResult(pattern="MACD_TRIPLE_GOLDEN", triggered=False, score=0, risk=["数据长度不足"])
    conditions = [
        _cross_up(ma5[-2], ma10[-2], ma5[-1], ma10[-1]),
        _cross_up(dif[-2], dea[-2], dif[-1], dea[-1]),
        _cross_up(vma5[-2], vma10[-2], vma5[-1], vma10[-1]),
    ]
    evidence = ["均线金叉", "MACD 金叉", "成交量均线金叉"]
    score = sum(conditions) * 30
    return SignalResult(
        pattern="MACD_TRIPLE_GOLDEN",
        triggered=all(conditions),
        score=min(score, 100),
        evidence=[item for ok, item in zip(conditions, evidence) if ok],
        risk=[] if all(conditions) else ["三金叉条件未完全满足"],
        entry_type="趋势确认",
    )


def detect_patterns(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    indicators: dict[str, Sequence[float | None]],
    patterns: Sequence[str] | None = None,
    sector_strength: float = 50,
    theme_strength: float = 50,
    rps_score: float | None = None,
) -> list[SignalResult]:
    selected = set(patterns or ["B1", "B2", "B3", "MACD_TRIPLE_GOLDEN"])
    results: list[SignalResult] = []
    if "B1" in selected:
        results.append(detect_b1(closes, lows, volumes, indicators, sector_strength, rps_score))
    if "B2" in selected:
        results.append(detect_b2(closes, highs, volumes, indicators, sector_strength))
    if "B3" in selected:
        results.append(detect_b3(closes, highs, volumes, indicators, theme_strength))
    if "MACD_TRIPLE_GOLDEN" in selected:
        results.append(detect_macd_triple_golden(indicators))
    return results


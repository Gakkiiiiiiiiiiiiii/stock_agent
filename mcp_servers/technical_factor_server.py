from __future__ import annotations

import logging
from datetime import date

from engines.market.data_provider import get_market_data_provider
from engines.technical.indicators import calc_all
from engines.technical.pattern_detector import detect_patterns

logger = logging.getLogger(__name__)


def calc_technical_indicators(symbol: str, end_date: str | None = None) -> dict:
    provider = get_market_data_provider()
    kline = provider.get_kline(symbol, end_date=date.fromisoformat(end_date) if end_date else None)
    invalid = _validate_kline(kline, symbol, minimum_records=30)
    if invalid is not None:
        return invalid
    highs = [item.high for item in kline.records]
    lows = [item.low for item in kline.records]
    closes = [item.close for item in kline.records]
    volumes = [item.volume for item in kline.records]
    return {"symbol": symbol, "indicators": calc_all(highs, lows, closes, volumes)}


def detect_pattern_signal(symbol: str, date: str | None = None, patterns: list[str] | None = None) -> dict:
    provider = get_market_data_provider()
    kline = provider.get_kline(symbol, end_date=__import__("datetime").date.fromisoformat(date) if date else None)
    invalid = _validate_kline(kline, symbol, minimum_records=30)
    if invalid is not None:
        return invalid
    highs = [item.high for item in kline.records]
    lows = [item.low for item in kline.records]
    closes = [item.close for item in kline.records]
    volumes = [item.volume for item in kline.records]
    indicators = calc_all(highs, lows, closes, volumes)
    signals = detect_patterns(closes, highs, lows, volumes, indicators, patterns=patterns, sector_strength=70, theme_strength=70)
    return {"symbol": symbol, "date": str(kline.records[-1].date), "signals": [item.model_dump() for item in signals]}


def scan_stock_signals(symbols: list[str], patterns: list[str] | None = None) -> dict:
    items = [detect_pattern_signal(symbol, patterns=patterns) for symbol in symbols]
    _merge_alpha_factors(items)
    return {"items": items}


def _merge_alpha_factors(items: list[dict]) -> None:
    """把因子库合成的 alpha 分数并入扫描结果，截面 top 10% 追加 ALPHA_TOP 信号。

    因子库为空或行情不可用时静默跳过，不影响原有形态信号。
    """
    try:
        from mcp_servers import factor_mining_server

        symbols = [item.get("symbol") for item in items if item.get("symbol")]
        if not symbols:
            return
        result = factor_mining_server.scan_alpha_factors(symbols)
        alpha_items = result.get("items") or []
        if not alpha_items:
            return
        by_symbol = {entry["symbol"]: entry for entry in alpha_items}
        n = len(alpha_items)
        top_cutoff = max(1, n // 10)
        for item in items:
            entry = by_symbol.get(item.get("symbol"))
            if entry is None or "signals" not in item:
                continue
            item["alpha_score"] = entry["alpha_score"]
            item["alpha_rank"] = entry["alpha_rank"]
            if entry["alpha_rank"] <= top_cutoff:
                score = max(0, min(100, round(100 * (1 - (entry["alpha_rank"] - 1) / max(n - 1, 1)))))
                item["signals"].append({
                    "pattern": "ALPHA_TOP",
                    "triggered": True,
                    "score": score,
                    "entry_type": "横截面因子优选",
                    "evidence": [
                        f"因子合成 alpha 分数 {entry['alpha_score']}，截面排名 {entry['alpha_rank']}/{n}",
                        f"基于 {entry.get('factor_count', 0)} 个样本内挖掘因子等权合成",
                    ],
                    "risk": ["样本内挖掘因子，存在过拟合风险，【待核验】"],
                    "confirm_condition": None,
                    "stop_condition": None,
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning("alpha 因子分数合并失败（不影响形态信号）: %s", exc)


def explain_signal(symbol: str, pattern: str) -> dict:
    result = detect_pattern_signal(symbol, patterns=[pattern])
    if "signals" not in result:
        return result
    return result["signals"][0] if result["signals"] else {"symbol": symbol, "pattern": pattern, "triggered": False}


def _validate_kline(kline, symbol: str, minimum_records: int) -> dict | None:
    if not kline.records:
        return {
            "symbol": symbol,
            "error": "未获取到可用日 K 数据",
            "data_source": kline.source,
            "warning": kline.warning,
        }
    if len(kline.records) < minimum_records:
        return {
            "symbol": symbol,
            "error": f"行情数据不足，至少需要 {minimum_records} 根 K 线",
            "data_source": kline.source,
            "warning": kline.warning,
        }
    return None

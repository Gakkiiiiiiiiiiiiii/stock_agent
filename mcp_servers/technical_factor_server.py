from __future__ import annotations

from datetime import date

from engines.market.data_provider import get_market_data_provider
from engines.technical.indicators import calc_all
from engines.technical.pattern_detector import detect_patterns


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
    return {"items": [detect_pattern_signal(symbol, patterns=patterns) for symbol in symbols]}


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

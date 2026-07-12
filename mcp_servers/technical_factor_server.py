from __future__ import annotations

from datetime import date

from engines.market.data_provider import get_market_data_provider
from engines.technical.indicators import calc_all
from engines.technical.pattern_detector import detect_patterns


def calc_technical_indicators(symbol: str, end_date: str | None = None) -> dict:
    provider = get_market_data_provider()
    kline = provider.get_kline(symbol, end_date=date.fromisoformat(end_date) if end_date else None)
    highs = [item.high for item in kline.records]
    lows = [item.low for item in kline.records]
    closes = [item.close for item in kline.records]
    volumes = [item.volume for item in kline.records]
    return {"symbol": symbol, "indicators": calc_all(highs, lows, closes, volumes)}


def detect_pattern_signal(symbol: str, date: str | None = None, patterns: list[str] | None = None) -> dict:
    provider = get_market_data_provider()
    kline = provider.get_kline(symbol, end_date=__import__("datetime").date.fromisoformat(date) if date else None)
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
    return result["signals"][0] if result["signals"] else {"symbol": symbol, "pattern": pattern, "triggered": False}

from engines.market.data_provider import sample_kline
from engines.technical.indicators import calc_all
from engines.technical.pattern_detector import detect_patterns


def test_detect_patterns_returns_evidence():
    records = sample_kline("TEST", days=140)
    highs = [item.high for item in records]
    lows = [item.low for item in records]
    closes = [item.close for item in records]
    volumes = [item.volume for item in records]
    indicators = calc_all(highs, lows, closes, volumes)
    signals = detect_patterns(closes, highs, lows, volumes, indicators, sector_strength=80, theme_strength=80, rps_score=88)
    assert {item.pattern for item in signals} >= {"B1", "B2", "B3", "MACD_TRIPLE_GOLDEN"}
    assert all(0 <= item.score <= 100 for item in signals)
    assert any(item.evidence or item.risk for item in signals)


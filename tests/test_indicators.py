from engines.technical.indicators import ema, kdj, ma, macd


def test_ma():
    assert ma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]


def test_ema_length_and_seed():
    result = ema([10, 12, 14], 2)
    assert len(result) == 3
    assert result[0] == 10
    assert result[-1] > result[0]


def test_macd_shape():
    result = macd([float(i) for i in range(1, 40)])
    assert set(result) == {"dif", "dea", "macd"}
    assert len(result["dif"]) == 39


def test_kdj_shape():
    highs = [10 + i for i in range(20)]
    lows = [8 + i for i in range(20)]
    closes = [9 + i for i in range(20)]
    result = kdj(highs, lows, closes)
    assert result["j"][0] is None
    assert result["j"][-1] is not None


from engines.content.financial_numeric import numeric_values_match, parse_financial_numerics


def _one(text):
    values = parse_financial_numerics(text)
    assert len(values) == 1, f"{text!r} -> {values}"
    return values[0]


def test_arabic_numbers():
    assert _one("上涨20%").value == 20
    assert _one("上涨20%").unit == "PERCENT"
    assert _one("增长30.5").value == 30.5
    assert _one("600000").value == 600000
    assert _one("PE 15倍").unit == "MULTIPLE"
    assert _one("3500点").unit == "POINT"
    yi = _one("成交100亿")
    assert yi.value == 100 and yi.unit == "CNY_YI"
    wan = _one("募资3000万")
    assert wan.value == 3000 and wan.unit == "CNY_WAN"


def test_chinese_numbers():
    yi = _one("市值一百五十亿")
    assert yi.value == 150 and yi.unit == "CNY_YI"
    percent = _one("百分之一百五")
    assert percent.value == 150 and percent.unit == "PERCENT"
    point = _one("三千四百点")
    assert point.value == 3400 and point.unit == "POINT"


def test_approximate_ranges_keep_interval_no_pseudo_precision():
    approx = _one("十来倍")
    assert approx.value is None  # 禁止伪精确：十来倍 绝不能变成 15
    assert (approx.min_value, approx.max_value) == (10, 19)
    assert approx.approximate is True
    assert approx.unit == "MULTIPLE"

    duo = _one("二十多个点")
    assert (duo.min_value, duo.max_value) == (20, 29)
    assert duo.unit == "POINT"

    ji = _one("十几个亿")
    assert (ji.min_value, ji.max_value) == (10, 19)
    assert ji.unit == "CNY_YI"


def test_comparators():
    lt = _one("不到两成")
    assert lt.comparator == "LT" and lt.value == 0.2 and lt.unit == "PERCENT"
    gt = _one("超过三成")
    assert gt.comparator == "GT" and gt.value == 0.3 and gt.unit == "PERCENT"


def test_metric_inference():
    assert _one("PE 15倍").metric == "PE"
    assert _one("净利润增长20%").metric == "PROFIT"
    assert _one("营收增长30%").metric == "REVENUE"


def test_numeric_values_match():
    approx = _one("十来倍")
    assert numeric_values_match(approx, _one("PE 15倍"))
    assert not numeric_values_match(approx, _one("PE 25倍"))
    assert numeric_values_match(_one("百分之一百五"), _one("150%"))
    # 单位不一致（% 对 倍）直接失败
    assert not numeric_values_match(_one("20%"), _one("20倍"))
    # 比较器方向语义
    assert numeric_values_match(_one("不到两成"), _one("0.1"))
    assert not numeric_values_match(_one("不到两成"), _one("0.5"))
    assert numeric_values_match(_one("超过三成"), _one("0.45"))

"""iFinD provider 测试：PIT 对齐（核心）、法定披露截止日近似、缓存降级。"""
import numpy as np
import pytest

from engines.market.ifind_provider import (
    IFindProvider,
    align_pit,
    disclosure_date,
    statutory_deadline,
)

DATES = [f"2026-04-{d:02d}" for d in range(27, 31)] + [f"2026-05-{d:02d}" for d in range(1, 7)]


def test_statutory_deadline():
    assert statutory_deadline("2025-03-31") == "2025-04-30"   # Q1
    assert statutory_deadline("2025-06-30") == "2025-08-31"   # 半年报
    assert statutory_deadline("2025-09-30") == "2025-10-31"   # Q3
    assert statutory_deadline("2025-12-31") == "2026-04-30"   # 年报（次年）
    assert statutory_deadline("20250331") == "2025-04-30"     # 兼容 YYYYMMDD


def test_disclosure_date_prefers_announce():
    rec = {"period_end": "2026-03-31", "announce_date": "2026-04-15"}
    assert disclosure_date(rec) == "2026-04-15"
    rec2 = {"period_end": "2026-03-31"}
    assert disclosure_date(rec2) == "2026-04-30"  # 无披露日按法定截止近似


def test_align_pit_no_value_before_disclosure():
    """披露日之前一律 NaN，披露日当日起生效并 ffill。"""
    records = [
        {"symbol": "600519", "period_end": "2026-03-31",
         "announce_date": "2026-04-29", "roe": 8.5, "gross_margin": 91.0},
    ]
    panels = align_pit(records, ["roe", "gross_margin"], DATES, ["600519.SH"])
    roe = panels["roe"][0]
    # 4-27/4-28 尚未披露
    assert np.isnan(roe[0]) and np.isnan(roe[1])
    # 4-29 披露日起生效
    assert roe[2] == 8.5
    # ffill 到窗口末尾
    assert roe[-1] == 8.5 and panels["gross_margin"][0, -1] == 91.0


def test_align_pit_takes_latest_disclosed_period():
    """新一期披露后切换取值；同一披露日挂多期取期末最新。"""
    records = [
        {"symbol": "600519", "period_end": "2025-12-31",
         "announce_date": "2026-04-28", "roe": 30.1},
        {"symbol": "600519", "period_end": "2026-03-31",
         "announce_date": "2026-04-28", "roe": 8.5},  # 同日披露，期末更新
        {"symbol": "600519", "period_end": "2026-06-30",
         "announce_date": "2026-08-20", "roe": 17.0},  # 窗口外，不应出现
    ]
    roe = align_pit(records, ["roe"], DATES, ["600519.SH"])["roe"][0]
    assert np.isnan(roe[0])            # 4-27 之前无披露
    assert roe[1] == 8.5               # 4-28 两期同日披露，取 Q1（期末最新）
    assert roe[-1] == 8.5              # 半年报未到披露日，不能泄露


def test_align_pit_statutory_fallback():
    """缺失 announce_date 时按法定截止日近似可见日。"""
    records = [{"symbol": "600519", "period_end": "2026-03-31", "roe": 8.5}]
    roe = align_pit(records, ["roe"], DATES, ["600519.SH"])["roe"][0]
    assert np.isnan(roe[2])            # 4-29 未到法定截止日，不可见
    assert roe[3] == 8.5               # 4-30 截止日（披露日 ≤ d）当日起可见
    assert roe[-1] == 8.5


def test_align_pit_realtime_only_excluded():
    """盈利预测类字段标记 realtime_only，不进 PIT 面板。"""
    records = [
        {"symbol": "600519", "period_end": "2026-03-31", "announce_date": "2026-04-28",
         "roe": 8.5, "eps_forecast": 70.0},
    ]
    panels = align_pit(records, ["roe", "eps_forecast"], DATES, ["600519.SH"])
    assert "roe" in panels
    assert "eps_forecast" not in panels  # 只进打分层


def test_align_pit_symbol_suffix_compatible():
    """records 用纯代码、symbols 带交易所后缀也能对上。"""
    records = [{"symbol": "600519", "period_end": "2026-03-31",
                "announce_date": "2026-04-28", "roe": 8.5}]
    roe = align_pit(records, ["roe"], DATES, ["600519.SH"])["roe"][0]
    assert roe[-1] == 8.5
    other = align_pit(records, ["roe"], DATES, ["000001.SZ"])["roe"][0]
    assert np.isnan(other).all()


class _FakeProvider(IFindProvider):
    """接口已接入的假 provider：返回固定模拟原始数据。"""

    def __init__(self, *args, records=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._records = records or []
        self.fetch_count = 0

    def fetch_financials(self, symbols, start, end):
        self.fetch_count += 1
        return list(self._records)


def test_cache_roundtrip_and_freshness(tmp_path):
    records = [{"symbol": "600519", "period_end": "2026-03-31",
                "announce_date": "2026-04-28", "roe": 8.5}]
    provider = _FakeProvider(cache_dir=tmp_path, records=records)
    got, warning = provider.get_financials(["600519.SH"], "2026-01-01", "2026-06-30")
    assert warning is None and len(got) == 1 and provider.fetch_count == 1
    # 第二次命中缓存，不再拉取
    got2, _ = provider.get_financials(["600519.SH"], "2026-01-01", "2026-06-30")
    assert provider.fetch_count == 1
    assert got2[0]["roe"] == 8.5 and got2[0]["symbol"] == "600519"
    # ttl=0 强制过期 → 重新拉取
    provider_ttl0 = _FakeProvider(cache_dir=tmp_path, cache_ttl_days=0, records=records)
    provider_ttl0.get_financials(["600519.SH"], "2026-01-01", "2026-06-30")
    assert provider_ttl0.fetch_count == 1


def test_unimplemented_fetch_degrades_to_warning(tmp_path):
    provider = IFindProvider(cache_dir=tmp_path)
    got, warning = provider.get_financials(["600519.SH"], "2026-01-01", "2026-06-30")
    assert got == [] and "未接入" in warning


def test_unimplemented_fetch_falls_back_to_stale_cache(tmp_path):
    records = [{"symbol": "600519", "period_end": "2026-03-31", "roe": 8.5}]
    fake = _FakeProvider(cache_dir=tmp_path, cache_ttl_days=0, records=records)
    fake.get_financials(["600519.SH"], "2026-01-01", "2026-06-30")  # 写入缓存（立即过期）
    provider = IFindProvider(cache_dir=tmp_path, cache_ttl_days=0)
    got, warning = provider.get_financials(["600519.SH"], "2026-01-01", "2026-06-30")
    assert len(got) == 1 and "过期缓存" in warning

"""MCP Market Data Server（收尾文档 §29）。

MCP -> QuantClient -> Quant。行情输入必须来自 Quant Market Snapshot，
禁止再直接调用 get_market_data_provider()。
Technical 指标（MA/MACD/RPS/Pattern/Trend）可继续计算，但输入来自 quant。
"""
from __future__ import annotations

from datetime import date, timedelta

from services.subsystems import get_quant_client

_BAR_FIELDS = ("open", "high", "low", "close", "volume", "amount")


def get_kline(symbol: str, start_date: str | None = None, end_date: str | None = None, freq: str = "1d", adjust: str = "qfq") -> dict:
    client = get_quant_client()
    end = str(end_date)[:10] if end_date else date.today().isoformat()
    start = str(start_date)[:10] if start_date else (date.fromisoformat(end) - timedelta(days=240)).isoformat()
    data = client.get_bars([symbol], start, end, adjust=adjust)
    dates = data.get("dates") or []
    bars = data.get("bars") or {}
    records = [
        {"date": day, **{field: (bars.get(field) or [[]])[0][index] for field in _BAR_FIELDS if bars.get(field)}}
        for index, day in enumerate(dates)
    ]
    return {
        "symbol": symbol,
        "freq": freq,
        "adjust": adjust,
        "records": records,
        "data_snapshot_id": data.get("data_snapshot_id"),
        "data_version": data.get("data_version"),
        "source": "quant",
    }


def get_market_snapshot() -> dict:
    # §29：快照必须来自 Quant（内容寻址、不可变）。
    client = get_quant_client()
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=30)).isoformat()
    data = client.get_bars(["000001.SH", "399001.SZ", "399006.SZ"], start, end)
    return {
        "source": "quant",
        "contract_version": "market-data.v1",
        "data_snapshot_id": data.get("data_snapshot_id"),
        "data_version": data.get("data_version"),
        "symbols": data.get("symbols"),
        "dates": data.get("dates"),
    }


def get_market_features() -> dict:
    # PIT 元数据（停牌/ST/退市）来自 quant security_status_daily。
    client = get_quant_client()
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=30)).isoformat()
    payload = client.get_market_features("000001.SH", start, end)
    return {"source": "quant", "items": payload.get("items", [])}


def get_sector_strength() -> dict:
    # quant 当前未提供行业强度 PIT 数据（§13 后续完善），显式返回空而不是本地估算。
    return {"sectors": [], "source": "quant", "warning": "sector strength not yet provided by quant"}


def get_theme_strength() -> dict:
    return {"themes": [], "source": "quant", "warning": "theme strength not yet provided by quant"}


def get_fund_flow() -> dict:
    return {"net_inflow": None, "warning": "MVP 暂未接入资金流数据源"}


def get_limit_up_stats() -> dict:
    return {"limit_up_count": None, "limit_down_count": None, "warning": "MVP 暂未接入涨跌停数据源"}

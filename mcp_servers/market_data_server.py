from __future__ import annotations

from datetime import date

from engines.market.data_provider import get_market_data_provider


def get_kline(symbol: str, start_date: str | None = None, end_date: str | None = None, freq: str = "1d", adjust: str = "qfq") -> dict:
    provider = get_market_data_provider()
    response = provider.get_kline(
        symbol=symbol,
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
        freq=freq,
        adjust=adjust,
    )
    return response.model_dump(mode="json")


def get_market_snapshot() -> dict:
    return get_market_data_provider().get_market_snapshot()


def get_sector_strength() -> dict:
    return {"sectors": get_market_data_provider().get_sector_strength()}


def get_theme_strength() -> dict:
    return {"themes": get_market_data_provider().get_sector_strength()}


def get_fund_flow() -> dict:
    return {"net_inflow": None, "warning": "MVP 暂未接入资金流数据源"}


def get_limit_up_stats() -> dict:
    return {"limit_up_count": None, "limit_down_count": None, "warning": "MVP 暂未接入涨跌停数据源"}

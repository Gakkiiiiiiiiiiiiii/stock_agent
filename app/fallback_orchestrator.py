"""Read-only degraded decision path.

When the model is unavailable this adapter can still present external
evidence, but it does not calculate technical indicators or produce market
facts locally.  Quant/content remain the respective authorities.
"""
from __future__ import annotations

import os
from datetime import date

from clients.content_client import RemoteContentClient
from clients.quant_client import RemoteQuantClient
from engines.market.trading_clock import TradingClock


class LocalFallbackOrchestrator:
    def __init__(self, *, clock: TradingClock | None = None) -> None:
        self.clock = clock or TradingClock()
        self.market = RemoteQuantClient(os.getenv("QUANT_SERVICE_URL") or os.getenv("MARKET_DATA_SERVICE_URL", "http://quant:8011"))
        self.content = RemoteContentClient(os.getenv("CONTENT_SERVICE_URL", "http://stock-content:8100"))

    def analyze_stock(self, symbol: str, as_of: date | None = None, patterns: list[str] | None = None) -> dict:
        evidence = self.market.get_technical_evidence(symbol, as_of=as_of.isoformat() if as_of else None)
        return {"symbol": symbol, "technical": evidence, "evidence_source": "quant", "orchestration": "remote-fallback"}

    def analyze_theme(self, theme_name: str) -> dict:
        evidence = self.content.search_video_knowledge(theme_name, limit=20)
        return {"theme_name": theme_name, "evidence": evidence, "evidence_source": "stock_content", "orchestration": "remote-fallback"}

    def daily_scan(self, scan_date: date | None = None, mode: str = "after_close") -> dict:
        snapshot = self.market.get_market_snapshot("latest")
        sectors = self.market.get_sector_strength(as_of=scan_date.isoformat() if scan_date else None)
        return {
            "date": str(scan_date or self.clock.current_trading_session("CN_A")),
            "mode": mode,
            "market_environment": snapshot,
            "top_themes": sectors.get("items", sectors) if isinstance(sectors, dict) else sectors,
            "evidence_source": "quant",
            "orchestration": "remote-fallback",
        }

from __future__ import annotations

from datetime import date

from engines.market.data_provider import get_market_data_provider
from engines.technical.indicators import calc_all
from engines.technical.pattern_detector import detect_patterns
from engines.theme.theme_score import rank_themes
from financial_agent.models import ThemeScoreInput
from storage.repositories.theme_repository import ThemeRepository


class LocalFallbackOrchestrator:
    def __init__(self) -> None:
        self.market = get_market_data_provider()
        self.themes = ThemeRepository()

    def analyze_stock(self, symbol: str, as_of: date | None = None, patterns: list[str] | None = None) -> dict:
        kline = self.market.get_kline(symbol, end_date=as_of)
        kline_guard = self._validate_kline_for_analysis(kline, as_of=as_of)
        if kline_guard is not None:
            return {"symbol": symbol, **kline_guard}
        if len(kline.records) < 30:
            return {"symbol": symbol, "error": "行情数据不足，至少需要 30 根 K 线"}
        highs = [item.high for item in kline.records]
        lows = [item.low for item in kline.records]
        closes = [item.close for item in kline.records]
        volumes = [item.volume for item in kline.records]
        indicators = calc_all(highs, lows, closes, volumes)
        signals = detect_patterns(closes, highs, lows, volumes, indicators, patterns=patterns, sector_strength=70, theme_strength=70)
        return {
            "symbol": symbol,
            "date": str(kline.records[-1].date),
            "technical": {
                "close": closes[-1],
                "ma20": indicators["ma20"][-1],
                "ltl": indicators["ltl"][-1],
                "kdj_j": indicators["kdj_j"][-1],
                "signals": [item.model_dump() for item in signals],
            },
            "summary": self._stock_summary(signals),
            "risk": {"warnings": sorted({warning for item in signals for warning in item.risk})},
            "orchestration": "local-fallback",
        }

    @staticmethod
    def _validate_kline_for_analysis(kline, as_of: date | None = None) -> dict | None:
        if not kline.records:
            return {
                "error": "QMT 未返回可用日 K 数据，已停止分析。",
                "data_source": kline.source,
                "warning": kline.warning,
            }
        latest_date = kline.records[-1].date
        reference_date = as_of or date.today()
        if kline.source != "qmt":
            return {
                "error": "当前返回的数据源不是 QMT 实时行情，已停止分析。",
                "data_source": kline.source,
                "latest_kline_date": str(latest_date),
                "warning": kline.warning,
            }
        if (reference_date - latest_date).days > 14:
            return {
                "error": "日 K 数据时间过旧，无法用于当前分析。",
                "data_source": kline.source,
                "latest_kline_date": str(latest_date),
                "warning": kline.warning,
            }
        return None

    def analyze_theme(self, theme_name: str) -> dict:
        theme = self.themes.search(theme_name)
        if not theme:
            return {"theme_name": theme_name, "exists": False, "summary": "知识库未找到该主题，可先补充核心逻辑、催化和证伪条件。"}
        score = rank_themes([ThemeScoreInput(theme=theme_name, knowledge_score=80, news_score=55, technical_score=55)])[0]
        return {"exists": True, "theme": theme.model_dump(), "score": score.model_dump(), "orchestration": "local-fallback"}

    def daily_scan(self, scan_date: date | None = None, mode: str = "after_close") -> dict:
        sectors = self.market.get_sector_strength()
        snapshot = self.market.get_market_snapshot()
        theme_scores = rank_themes(
            [
                ThemeScoreInput(theme=item["sector"], price_strength_score=item["strength_score"], technical_score=item["strength_score"], news_score=60)
                for item in sectors
            ]
        )
        return {
            "date": str(scan_date or date.today()),
            "mode": mode,
            "market_environment": {
                "market_regime": snapshot.get("market_regime"),
                "risk_appetite": snapshot.get("risk_appetite"),
                "suggested_position": "50%-70%",
                "warnings": [snapshot.get("warning")] if snapshot.get("warning") else [],
            },
            "top_themes": [item.model_dump() for item in theme_scores],
            "watch_points": ["观察成交额是否继续放大", "避免追高高位放量滞涨标的"],
            "orchestration": "local-fallback",
        }

    @staticmethod
    def _stock_summary(signals) -> str:
        triggered = [item for item in signals if item.triggered]
        if not triggered:
            return "当前未出现高置信 B1/B2/B3 或三金叉信号，宜等待更明确确认。"
        names = "、".join(item.pattern for item in triggered)
        return f"当前触发 {names}，仍需结合行业强度、成交额和证伪条件执行。"

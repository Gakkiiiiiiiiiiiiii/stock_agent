from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

from engines.content.external_verification.base import claim_numbers, extract_ticker, make_result

logger = logging.getLogger(__name__)

# 价格类 predicate（§26/§6.1：PRICE_LEVEL → market data；FACT → predicate 路由）。
# 仅保留能用最新收盘价真实验证的谓词；target_price 是预测、market_cap 需要股本，
# close 验证不了，交由其它 provider 或落 NOT_FOUND。
PRICE_PREDICATES = {
    "price_level",
    "price",
    "latest_price",
    "close_price",
    "support_level",
    "resistance_level",
}
# §6.1：market provider 只处理真实行情字段，VALUATION 改由 fundamental provider 处理，
# 避免 "PE 约 20 倍" 被拿去和 close（如 300 元）比对的误判。
MARKET_KINDS = {"PRICE_LEVEL"}


class MarketDataVerificationProvider:
    """用项目现有行情设施（engines.market.data_provider）核对价格类 claim。

    - 行情客户端惰性加载：worker 环境若 QMT 桥不可用，verify() 返回 ERROR（不静默 MATCH）。
    - 测试可注入 fake 客户端（实现 get_kline(symbol) -> 带 records 的对象/dict）。
    - 查不到数据 -> NOT_FOUND；有数据但数值超容差 -> CONFLICT；容差内 -> MATCH。
    - 容差默认 ±5%，可用 VIDEO_EXTERNAL_VERIFY_TOLERANCE 覆盖；claim 为区间时按区间重叠判断。
    """

    def __init__(self, market_client: Any | None = None, tolerance: float | None = None) -> None:
        self._market_client = market_client
        if tolerance is None:
            try:
                tolerance = float(os.getenv("VIDEO_EXTERNAL_VERIFY_TOLERANCE", "0.05"))
            except ValueError:
                tolerance = 0.05
        self.tolerance = max(0.0, tolerance)

    def supports(self, unit: dict[str, Any]) -> bool:
        if extract_ticker(unit) is None:
            return False
        kind = str(unit.get("knowledge_kind") or "").upper()
        if kind in MARKET_KINDS:
            return True
        if kind == "FACT":
            return str(unit.get("predicate_key") or "").strip().lower() in PRICE_PREDICATES
        return False

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        ticker = extract_ticker(unit)
        if ticker is None:
            return make_result("NOT_FOUND", source_type="MARKET_DATA", provider="market_data", reason="NO_TICKER")
        numbers = claim_numbers(str(unit.get("statement") or ""))
        if not numbers:
            return make_result("NOT_FOUND", source_type="MARKET_DATA", provider="market_data", source_id=ticker, reason="NO_CLAIM_NUMBER")
        try:
            client = self._client()
            close, as_of = self._latest_close(client, ticker)
        except Exception as exc:  # worker 环境优雅降级，绝不把不可用当 MATCH
            logger.warning("行情数据获取失败 ticker=%s: %s", ticker, exc)
            return make_result("ERROR", source_type="MARKET_DATA", provider="market_data", source_id=ticker, reason="MARKET_DATA_UNAVAILABLE", provenance={"error": str(exc)})
        if close is None or close <= 0:
            return make_result("NOT_FOUND", source_type="MARKET_DATA", provider="market_data", source_id=ticker, reason="NO_MARKET_DATA")
        matched = any(abs(number - close) <= self.tolerance * close for number in numbers)
        return make_result(
            "MATCH" if matched else "CONFLICT",
            source_type="MARKET_DATA",
            provider="market_data",
            source_id=ticker,
            as_of=as_of,
            observed_value=close,
            unit="CNY",
            provenance={"claim_values": numbers, "tolerance": self.tolerance},
        )

    def _client(self) -> Any:
        if self._market_client is not None:
            return self._market_client
        # 惰性 import：避免 worker 环境在模块加载期就拉起行情桥。
        from engines.market.data_provider import get_market_data_provider

        self._market_client = get_market_data_provider()
        return self._market_client

    @staticmethod
    def _latest_close(client: Any, ticker: str) -> tuple[float | None, str | None]:
        response = client.get_kline(ticker)
        records = response.get("records") if isinstance(response, dict) else getattr(response, "records", [])
        records = list(records or [])
        if not records:
            return None, None
        last = records[-1]
        if isinstance(last, dict):
            close = last.get("close")
            day = last.get("date") or last.get("trading_date")
        else:
            close = getattr(last, "close", None)
            day = getattr(last, "date", None)
        if close is None:
            return None, None
        as_of = day.isoformat() if isinstance(day, date) else (str(day) if day else None)
        return float(close), as_of

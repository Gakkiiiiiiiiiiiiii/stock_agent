from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from engines.market.qmt_bridge_client import QmtBridgeClient, QmtBridgeError
from engines.backtest.execution import price_limit_pct
from financial_agent.models import KlineRecord, KlineResponse


COMMON_SYMBOL_ALIASES = {
    "黄金etf": "518880",
    "黄金etf华夏": "518850",
    "长江电力": "600900",
    "中国移动": "600941",
    "中国神华": "601088",
    "工商银行": "601398",
    "山东黄金": "600547",
    "紫金矿业": "601899",
    "中际旭创": "300308",
    "新易盛": "300502",
    "工业富联": "601138",
    "药明康德": "603259",
    "恒瑞医药": "600276",
    "迈瑞医疗": "300760",
    "洛阳钼业": "603993",
    "江西铜业": "600362",
}


class MarketDataProvider:
    def get_kline(self, symbol: str, start_date: date | None = None, end_date: date | None = None, freq: str = "1d", adjust: str = "qfq") -> KlineResponse:
        raise NotImplementedError

    def get_market_snapshot(self, as_of: date | None = None, force_refresh: bool = False) -> dict[str, Any]:
        raise NotImplementedError

    def get_sector_strength(self, top_k: int = 20, as_of: date | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError


class QmtMarketDataProvider(MarketDataProvider):
    def __init__(self, bridge: QmtBridgeClient | None = None) -> None:
        self.bridge = bridge or QmtBridgeClient()

    def get_kline(self, symbol: str, start_date: date | None = None, end_date: date | None = None, freq: str = "1d", adjust: str = "qfq") -> KlineResponse:
        resolved = self.resolve_symbol(symbol)
        qmt_symbol = to_qmt_symbol(resolved)
        actual_start = start_date or (date.today() - timedelta(days=240))
        actual_end = end_date or date.today()
        try:
            rows = self.bridge.get_history(
                symbols=[qmt_symbol],
                period=self._normalize_period(freq),
                start_time=actual_start.strftime("%Y%m%d"),
                end_time=actual_end.strftime("%Y%m%d"),
                dividend_type=self._normalize_adjust(adjust),
                fill_data=True,
                prefer_cache_first=True,
            )
        except QmtBridgeError as exc:
            return KlineResponse(
                symbol=qmt_symbol,
                freq=freq,
                adjust=adjust,
                records=[],
                source="qmt",
                warning=str(exc),
            )
        records = self._records_from_rows(rows, symbol=qmt_symbol, start_date=start_date, end_date=end_date)
        warning = None
        if not records:
            warning = f"QMT 未返回 {qmt_symbol} 在 {actual_start} 到 {actual_end} 的有效 {freq} 数据。"
        return KlineResponse(
            symbol=qmt_symbol,
            freq=freq,
            adjust=adjust,
            records=records,
            source="qmt",
            warning=warning,
        )

    def get_market_snapshot(self, as_of: date | None = None, force_refresh: bool = False) -> dict[str, Any]:
        _ = force_refresh
        if as_of and as_of != date.today():
            return {
                "market_regime": "未知",
                "risk_appetite": "未知",
                "warning": "HISTORICAL_MARKET_SNAPSHOT_UNAVAILABLE",
                "quality_score": 0.0,
                "quality_flags": ["HISTORICAL_MARKET_SNAPSHOT_UNAVAILABLE"],
                "source": "qmt",
            }
        index_symbols = ["000001.SH", "399001.SZ", "399006.SZ"]
        end_day = as_of or date.today()
        start_day = end_day - timedelta(days=40)
        try:
            rows = self.bridge.get_history(
                symbols=index_symbols,
                period="1d",
                start_time=start_day.strftime("%Y%m%d"),
                end_time=end_day.strftime("%Y%m%d"),
                dividend_type="front",
                fill_data=True,
                prefer_cache_first=True,
            )
            quotes = self.bridge.get_quotes(index_symbols)
        except QmtBridgeError as exc:
            return {
                "market_regime": "未知",
                "risk_appetite": "未知",
                "turnover": None,
                "up_count": None,
                "down_count": None,
                "limit_up_count": None,
                "limit_down_count": None,
                "warning": str(exc),
                "source": "qmt",
            }
        grouped = group_history_rows(rows)
        intraday_changes: list[float] = []
        return_5d: list[float] = []
        return_20d: list[float] = []
        volatility_20d: list[float] = []
        drawdown_20d: list[float] = []
        for symbol in index_symbols:
            quote = quotes.get(symbol) or {}
            last_price = safe_float(quote.get("last_price"))
            last_close = safe_float(quote.get("last_close"))
            if last_price > 0 and last_close > 0:
                intraday_changes.append((last_price - last_close) / last_close * 100)
            records = grouped.get(symbol, [])
            pct_5d = calculate_return_pct(records, 5)
            pct_20d = calculate_return_pct(records, 20)
            if pct_5d is not None:
                return_5d.append(pct_5d)
            if pct_20d is not None:
                return_20d.append(pct_20d)
            vol = calculate_volatility_pct(records, 20)
            dd = calculate_drawdown_pct(records, 20)
            if vol is not None:
                volatility_20d.append(vol)
            if dd is not None:
                drawdown_20d.append(dd)
        intraday = average(intraday_changes)
        avg_5d = average(return_5d)
        avg_20d = average(return_20d)
        avg_vol_20d = average(volatility_20d)
        avg_dd_20d = average(drawdown_20d)
        breadth = self._build_market_breadth()
        market_regime, risk_appetite = classify_market_snapshot(intraday=intraday, avg_5d=avg_5d, avg_20d=avg_20d)
        warning = None
        if not grouped:
            warning = "QMT 未返回指数历史数据，市场快照仅保留空结构。"
        quality_flags = list(breadth.get("quality_flags") or [])
        quote_coverage = breadth.get("quote_coverage")
        quality_score = 0.0 if quote_coverage is not None and quote_coverage < 0.9 else None
        return {
            "market_regime": market_regime,
            "risk_appetite": risk_appetite,
            "turnover": breadth.get("turnover_amount"),
            "turnover_amount": breadth.get("turnover_amount"),
            "universe_size": breadth.get("universe_size"),
            "up_count": breadth.get("up_count"),
            "down_count": breadth.get("down_count"),
            "limit_up_count": breadth.get("limit_up_count"),
            "limit_down_count": breadth.get("limit_down_count"),
            "top10_amount_share": breadth.get("top10_amount_share"),
            "requested_quote_count": breadth.get("requested_quote_count"),
            "received_quote_count": breadth.get("received_quote_count"),
            "quote_coverage": quote_coverage,
            "quality_flags": quality_flags,
            "quality_score": quality_score,
            "warning": warning,
            "source": "qmt",
            "indices": {
                "intraday_pct": safe_round(intraday, 2),
                "return_5d_pct": safe_round(avg_5d, 2),
                "return_20d_pct": safe_round(avg_20d, 2),
                "volatility_20d_pct": safe_round(avg_vol_20d, 2),
                "drawdown_20d_pct": safe_round(avg_dd_20d, 2),
            },
        }

    def _build_market_breadth(self) -> dict[str, Any]:
        try:
            rows = self.bridge.get_industry_map(symbols=[], sector_prefix="GICS2", only_a_share=True)
        except QmtBridgeError:
            return {}
        symbols = sorted({str(row.get("symbol") or "").strip() for row in rows if row.get("symbol")})
        if not symbols:
            return {}
        quotes: dict[str, Any] = {}
        for chunk in batched(symbols, 200):
            try:
                quotes.update(self.bridge.get_quotes(chunk))
            except QmtBridgeError:
                continue
        up_count = down_count = limit_up_count = limit_down_count = 0
        amounts: list[float] = []
        for symbol, payload in quotes.items():
            last_price = safe_float(payload.get("last_price") or payload.get("price"))
            last_close = safe_float(payload.get("last_close") or payload.get("pre_close"))
            amount = safe_float(payload.get("amount") or payload.get("turnover") or 0)
            if amount > 0:
                amounts.append(amount)
            if last_price <= 0 or last_close <= 0:
                continue
            pct = (last_price - last_close) / last_close
            if pct > 0:
                up_count += 1
            elif pct < 0:
                down_count += 1
            limit = price_limit_pct(symbol=str(payload.get("symbol") or symbol), is_st=is_st_quote(payload))
            if pct >= limit - 0.002:
                limit_up_count += 1
            elif pct <= -limit + 0.002:
                limit_down_count += 1
        total_amount = sum(amounts)
        top10_share = sum(sorted(amounts, reverse=True)[:10]) / total_amount if total_amount > 0 else None
        requested_quote_count = len(symbols)
        received_quote_count = len(quotes)
        quote_coverage = received_quote_count / requested_quote_count if requested_quote_count else None
        quality_flags = ["MARKET_QUOTE_COVERAGE_LOW"] if quote_coverage is not None and quote_coverage < 0.9 else []
        return {
            "universe_size": len(symbols),
            "requested_quote_count": requested_quote_count,
            "received_quote_count": received_quote_count,
            "quote_coverage": round(quote_coverage, 6) if quote_coverage is not None else None,
            "up_count": up_count,
            "down_count": down_count,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "turnover_amount": round(total_amount, 2) if total_amount else None,
            "top10_amount_share": round(top10_share, 6) if top10_share is not None else None,
            "quality_flags": quality_flags,
        }

    def get_sector_strength(self, top_k: int = 20, as_of: date | None = None) -> list[dict[str, Any]]:
        if as_of and as_of != date.today():
            return []
        try:
            rows = self.bridge.get_industry_map(symbols=[], sector_prefix="GICS2", only_a_share=True)
        except QmtBridgeError:
            return []
        sector_samples: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            sector_name = str(row.get("industry_name") or row.get("industry_code") or "").strip() or "未分类"
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            if len(sector_samples[sector_name]) < 3:
                sector_samples[sector_name].append(symbol)
        sample_symbols = sorted({symbol for symbols in sector_samples.values() for symbol in symbols})
        if not sample_symbols:
            return []
        quotes: dict[str, Any] = {}
        for chunk in batched(sample_symbols, 200):
            try:
                quotes.update(self.bridge.get_quotes(chunk))
            except QmtBridgeError:
                continue
        items: list[dict[str, Any]] = []
        for sector_name, symbols in sector_samples.items():
            change_pcts: list[float] = []
            up_count = 0
            down_count = 0
            for symbol in symbols:
                payload = quotes.get(symbol) or {}
                last_price = safe_float(payload.get("last_price"))
                last_close = safe_float(payload.get("last_close"))
                if last_price <= 0 or last_close <= 0:
                    continue
                pct = (last_price - last_close) / last_close * 100
                change_pcts.append(pct)
                if pct > 0:
                    up_count += 1
                elif pct < 0:
                    down_count += 1
            if not change_pcts:
                continue
            avg_pct = average(change_pcts)
            breadth = (up_count - down_count) / max(len(change_pcts), 1)
            score = clamp(50 + avg_pct * 6 + breadth * 20, 0, 100)
            items.append(
                {
                    "sector": sector_name,
                    "strength_score": round(score, 2),
                    "reason": f"样本涨跌幅均值 {avg_pct:.2f}%，上涨/下跌 {up_count}/{down_count}",
                    "change_pct": round(avg_pct, 2),
                }
            )
        return sorted(items, key=lambda item: item["strength_score"], reverse=True)[:top_k]

    @staticmethod
    def resolve_symbol(symbol: str) -> str:
        text = normalize_text(symbol)
        if text in COMMON_SYMBOL_ALIASES:
            return COMMON_SYMBOL_ALIASES[text]
        if "." in str(symbol):
            code, suffix = str(symbol).split(".", 1)
            if code.isdigit() and suffix.lower() in {"sh", "sz", "bj"}:
                return f"{code}.{suffix.upper()}"
        return str(symbol).strip()

    @staticmethod
    def _normalize_period(freq: str) -> str:
        mapping = {
            "1d": "1d",
            "d": "1d",
            "daily": "1d",
            "day": "1d",
        }
        return mapping.get((freq or "1d").lower(), "1d")

    @staticmethod
    def _normalize_adjust(adjust: str) -> str:
        mapping = {
            "qfq": "front",
            "hfq": "back",
            "bfq": "none",
            "none": "none",
            "nfq": "none",
        }
        return mapping.get((adjust or "qfq").lower(), "front")

    @staticmethod
    def _records_from_rows(
        rows: list[dict[str, Any]],
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[KlineRecord]:
        records: list[KlineRecord] = []
        for row in rows:
            row_symbol = str(row.get("symbol") or symbol or "").strip()
            if symbol and row_symbol and row_symbol != symbol:
                continue
            trading_day = parse_date_value(row.get("trading_date"))
            if start_date and trading_day < start_date:
                continue
            if end_date and trading_day > end_date:
                continue
            records.append(
                KlineRecord(
                    date=trading_day,
                    open=safe_float(row.get("open")),
                    high=safe_float(row.get("high")),
                    low=safe_float(row.get("low")),
                    close=safe_float(row.get("close")),
                    volume=safe_float(row.get("volume")),
                    amount=safe_float(row.get("amount")),
                )
            )
        return sorted(records, key=lambda item: item.date)


@lru_cache(maxsize=4)
def get_market_data_provider() -> MarketDataProvider:
    return QmtMarketDataProvider()


def normalize_text(value: str) -> str:
    return "".join(str(value).strip().lower().split())


def parse_date_value(value: Any) -> date:
    text = str(value).split(" ")[0].replace("/", "-")
    return date.fromisoformat(text)


def safe_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(str(value).replace(",", ""))


def to_qmt_symbol(symbol: str) -> str:
    raw = str(symbol).strip()
    if "." in raw:
        code, suffix = raw.split(".", 1)
        if code.isdigit() and suffix.lower() in {"sh", "sz", "bj"}:
            return f"{code}.{suffix.upper()}"
    if not raw.isdigit() or len(raw) != 6:
        return raw
    if raw.startswith(("60", "68", "50", "51", "56", "58")):
        return f"{raw}.SH"
    if raw.startswith(("00", "12", "15", "16", "18", "20", "30")):
        return f"{raw}.SZ"
    if raw.startswith(("43", "83", "87", "88", "92")):
        return f"{raw}.BJ"
    return f"{raw}.SZ"


def average(values: list[float]) -> float:
    if not values:
        return None
    return sum(values) / len(values)


def safe_round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def calculate_return_pct(records: list[KlineRecord], lookback: int) -> float | None:
    if len(records) <= lookback:
        return None
    base = records[-lookback - 1].close
    if base <= 0:
        return None
    return (records[-1].close - base) / base * 100


def calculate_volatility_pct(records: list[KlineRecord], lookback: int) -> float | None:
    if len(records) <= lookback:
        return None
    returns = []
    window = records[-lookback - 1 :]
    for prev, cur in zip(window, window[1:], strict=False):
        if prev.close > 0 and cur.close > 0:
            returns.append(cur.close / prev.close - 1)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    return (variance ** 0.5) * 100


def calculate_drawdown_pct(records: list[KlineRecord], lookback: int) -> float | None:
    if len(records) <= lookback:
        return None
    closes = [item.close for item in records[-lookback:] if item.close > 0]
    if not closes:
        return None
    peak = max(closes)
    if peak <= 0:
        return None
    return (closes[-1] - peak) / peak * 100


def classify_market_snapshot(intraday: float | None, avg_5d: float | None, avg_20d: float | None) -> tuple[str, str]:
    if intraday is None or avg_5d is None or avg_20d is None:
        return "未知", "未知"
    if avg_20d >= 5 and avg_5d >= 1 and intraday >= 0:
        return "强势上行", "较高"
    if avg_20d >= 2 and avg_5d >= 0:
        return "震荡偏强", "中等"
    if avg_20d <= -4 and avg_5d <= -1:
        return "弱势承压", "较低"
    if avg_5d < 0 or intraday < 0:
        return "震荡偏弱", "偏低"
    return "震荡整理", "中等"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def batched(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def is_st_quote(payload: dict[str, Any]) -> bool:
    text = str(payload.get("name") or payload.get("stock_name") or payload.get("instrument_name") or "")
    return "ST" in text.upper() or "＊ST" in text.upper() or "*ST" in text.upper()


def group_history_rows(rows: list[dict[str, Any]]) -> dict[str, list[KlineRecord]]:
    grouped: dict[str, list[KlineRecord]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        grouped[symbol].extend(QmtMarketDataProvider._records_from_rows([row], symbol=symbol))
    return grouped


def sample_kline(symbol: str, days: int = 140) -> list[KlineRecord]:
    base = date(2026, 1, 1)
    records: list[KlineRecord] = []
    close = 20.0
    for i in range(days):
        drift = 0.05 if i < 80 else (-0.03 if i < 115 else 0.08)
        wave = ((i % 9) - 4) * 0.03
        prev = close
        close = max(1.0, close + drift + wave)
        high = max(prev, close) * 1.02
        low = min(prev, close) * 0.98
        volume = 1_000_000 * (1 + (i % 7) / 10)
        if i > 120:
            volume *= 1.4
        records.append(
            KlineRecord(
                date=date.fromordinal(base.toordinal() + i),
                open=round(prev, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=round(volume, 2),
                amount=round(volume * close, 2),
                turnover_rate=round(1 + (i % 5) * 0.2, 2),
            )
        )
    return records

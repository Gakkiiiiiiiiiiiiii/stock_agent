from __future__ import annotations

import csv
import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from financial_agent.models import KlineRecord, KlineResponse
from financial_agent.utils import project_root


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

    def get_market_snapshot(self) -> dict[str, Any]:
        raise NotImplementedError

    def get_sector_strength(self, top_k: int = 20) -> list[dict[str, Any]]:
        raise NotImplementedError


class LocalCsvMarketDataProvider(MarketDataProvider):
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or project_root() / "data" / "market"

    def get_kline(self, symbol: str, start_date: date | None = None, end_date: date | None = None, freq: str = "1d", adjust: str = "qfq") -> KlineResponse:
        resolved = self.resolve_symbol(symbol)
        path = self.data_dir / f"{resolved}.csv"
        if not path.exists():
            return KlineResponse(symbol=resolved, freq=freq, adjust=adjust, records=sample_kline(resolved))
        records: list[KlineRecord] = []
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                record = KlineRecord(
                    date=date.fromisoformat(row["date"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                    amount=float(row.get("amount") or 0),
                    turnover_rate=float(row["turnover_rate"]) if row.get("turnover_rate") else None,
                )
                if start_date and record.date < start_date:
                    continue
                if end_date and record.date > end_date:
                    continue
                records.append(record)
        return KlineResponse(symbol=resolved, freq=freq, adjust=adjust, records=records)

    def get_market_snapshot(self) -> dict[str, Any]:
        return {
            "market_regime": "震荡偏强",
            "risk_appetite": "中等",
            "turnover": None,
            "up_count": None,
            "down_count": None,
            "limit_up_count": None,
            "limit_down_count": None,
            "warning": "当前使用本地样例行情；设置 MARKET_DATA_PROVIDER=akshare 可切换到实时 A 股行情。",
            "source": "local_csv",
        }

    def get_sector_strength(self, top_k: int = 20) -> list[dict[str, Any]]:
        from engines.market.sector_strength import sample_sector_strength

        return sample_sector_strength()[:top_k]

    @staticmethod
    def resolve_symbol(symbol: str) -> str:
        key = normalize_text(symbol)
        return COMMON_SYMBOL_ALIASES.get(key, symbol)


class AkshareMarketDataProvider(MarketDataProvider):
    def __init__(self, fallback: MarketDataProvider | None = None) -> None:
        import akshare as ak

        self.ak = ak
        self.fallback = fallback or LocalCsvMarketDataProvider()

    def get_kline(self, symbol: str, start_date: date | None = None, end_date: date | None = None, freq: str = "1d", adjust: str = "qfq") -> KlineResponse:
        try:
            code, security_type = self.resolve_symbol(symbol)
            period = self._normalize_period(freq)
            start = (start_date or date.today().replace(month=1, day=1)).strftime("%Y%m%d")
            end = (end_date or date.today()).strftime("%Y%m%d")
            hist = self._fetch_hist(code=code, security_type=security_type, period=period, adjust=adjust, start=start, end=end)
            records = self._records_from_dataframe(hist, start_date=start_date, end_date=end_date)
            if records:
                return KlineResponse(symbol=code, freq=freq, adjust=adjust, records=records)
        except Exception:
            pass
        return self.fallback.get_kline(symbol=symbol, start_date=start_date, end_date=end_date, freq=freq, adjust=adjust)

    def get_market_snapshot(self) -> dict[str, Any]:
        try:
            spot = self.ak.stock_zh_a_spot_em()
            change_col = pick_column(spot.columns, "涨跌幅")
            turnover_col = pick_column(spot.columns, "成交额")
            up_count = int((spot[change_col] > 0).sum())
            down_count = int((spot[change_col] < 0).sum())
            limit_up_count = int((spot[change_col] >= 9.8).sum())
            limit_down_count = int((spot[change_col] <= -9.8).sum())
            turnover = round(float(spot[turnover_col].fillna(0).sum()) / 100000000, 2)
            breadth = up_count / max(up_count + down_count, 1)
            if breadth >= 0.62 and limit_up_count >= 45:
                market_regime = "强势上行"
                risk_appetite = "较高"
            elif breadth >= 0.54:
                market_regime = "震荡偏强"
                risk_appetite = "中等"
            elif breadth <= 0.42:
                market_regime = "弱势承压"
                risk_appetite = "较低"
            else:
                market_regime = "震荡偏弱"
                risk_appetite = "偏低"
            return {
                "market_regime": market_regime,
                "risk_appetite": risk_appetite,
                "turnover": turnover,
                "up_count": up_count,
                "down_count": down_count,
                "limit_up_count": limit_up_count,
                "limit_down_count": limit_down_count,
                "warning": None,
                "source": "akshare_eastmoney",
            }
        except Exception:
            return self.fallback.get_market_snapshot()

    def get_sector_strength(self, top_k: int = 20) -> list[dict[str, Any]]:
        try:
            board = self.ak.stock_board_industry_name_em()
            name_col = pick_column(board.columns, "板块名称", "名称")
            pct_col = pick_column(board.columns, "涨跌幅")
            up_col = optional_column(board.columns, "上涨家数")
            down_col = optional_column(board.columns, "下跌家数")
            amount_col = optional_column(board.columns, "总市值", "成交额")
            items: list[dict[str, Any]] = []
            for _, row in board.iterrows():
                pct = safe_float(row[pct_col])
                up_count = safe_float(row[up_col]) if up_col else 0.0
                down_count = safe_float(row[down_col]) if down_col else 0.0
                breadth = 0.0
                if up_col and down_col and (up_count + down_count) > 0:
                    breadth = (up_count - down_count) / (up_count + down_count)
                size_factor = 0.0
                if amount_col:
                    size_factor = min(safe_float(row[amount_col]) / 1_000_000_000_000, 1.0)
                score = max(0.0, min(100.0, round(50 + pct * 6 + breadth * 20 + size_factor * 5, 2)))
                reason = f"涨跌幅 {pct:.2f}%"
                if up_col and down_col:
                    reason += f"，上涨/下跌家数 {int(up_count)}/{int(down_count)}"
                items.append({"sector": str(row[name_col]), "strength_score": score, "reason": reason, "change_pct": round(pct, 2)})
            return sorted(items, key=lambda item: item["strength_score"], reverse=True)[:top_k]
        except Exception:
            return self.fallback.get_sector_strength(top_k=top_k)

    def resolve_symbol(self, symbol: str) -> tuple[str, str]:
        text = normalize_text(symbol)
        if text in COMMON_SYMBOL_ALIASES:
            code = COMMON_SYMBOL_ALIASES[text]
            return code, detect_security_type(code, symbol)
        if symbol.isdigit() and len(symbol) == 6:
            return symbol, detect_security_type(symbol, symbol)
        code = self._search_code_by_name(symbol)
        if code:
            return code, detect_security_type(code, symbol)
        return symbol, detect_security_type(symbol, symbol)

    def _search_code_by_name(self, name: str) -> str | None:
        target = normalize_text(name)
        for loader in (self.ak.stock_zh_a_spot_em, self.ak.fund_etf_spot_em):
            try:
                frame = loader()
                name_col = pick_column(frame.columns, "名称")
                code_col = pick_column(frame.columns, "代码")
                matched = frame[frame[name_col].astype(str).map(normalize_text) == target]
                if not matched.empty:
                    return str(matched.iloc[0][code_col])
            except Exception:
                continue
        return None

    def _fetch_hist(self, code: str, security_type: str, period: str, adjust: str, start: str, end: str):
        if security_type == "etf":
            return self.ak.fund_etf_hist_em(symbol=code, period=period, start_date=start, end_date=end, adjust=adjust)
        return self.ak.stock_zh_a_hist(symbol=code, period=period, start_date=start, end_date=end, adjust=adjust)

    @staticmethod
    def _normalize_period(freq: str) -> str:
        mapping = {
            "1d": "daily",
            "d": "daily",
            "daily": "daily",
            "1w": "weekly",
            "w": "weekly",
            "weekly": "weekly",
            "1m": "monthly",
            "m": "monthly",
            "monthly": "monthly",
        }
        return mapping.get((freq or "1d").lower(), "daily")

    @staticmethod
    def _records_from_dataframe(frame, start_date: date | None = None, end_date: date | None = None) -> list[KlineRecord]:
        records: list[KlineRecord] = []
        if frame is None or getattr(frame, "empty", True):
            return records
        date_col = pick_column(frame.columns, "日期", "date")
        open_col = pick_column(frame.columns, "开盘", "open")
        high_col = pick_column(frame.columns, "最高", "high")
        low_col = pick_column(frame.columns, "最低", "low")
        close_col = pick_column(frame.columns, "收盘", "close")
        volume_col = optional_column(frame.columns, "成交量", "volume")
        amount_col = optional_column(frame.columns, "成交额", "amount")
        turnover_col = optional_column(frame.columns, "换手率", "turnover_rate")
        for _, row in frame.iterrows():
            trading_day = parse_date_value(row[date_col])
            if start_date and trading_day < start_date:
                continue
            if end_date and trading_day > end_date:
                continue
            records.append(
                KlineRecord(
                    date=trading_day,
                    open=safe_float(row[open_col]),
                    high=safe_float(row[high_col]),
                    low=safe_float(row[low_col]),
                    close=safe_float(row[close_col]),
                    volume=safe_float(row[volume_col]) if volume_col else 0.0,
                    amount=safe_float(row[amount_col]) if amount_col else 0.0,
                    turnover_rate=safe_float(row[turnover_col]) if turnover_col else None,
                )
            )
        return records


@lru_cache(maxsize=4)
def get_market_data_provider() -> MarketDataProvider:
    provider = os.getenv("MARKET_DATA_PROVIDER", "local_csv").strip().lower()
    if provider in {"akshare", "ak"}:
        try:
            return AkshareMarketDataProvider()
        except Exception:
            return LocalCsvMarketDataProvider()
    return LocalCsvMarketDataProvider()


def normalize_text(value: str) -> str:
    return "".join(str(value).strip().lower().split())


def detect_security_type(code: str, raw_symbol: str) -> str:
    text = normalize_text(raw_symbol)
    if "etf" in text or (str(code).isdigit() and str(code).startswith(("1", "5"))):
        return "etf"
    return "stock"


def pick_column(columns: Any, *candidates: str) -> str:
    normalized = {normalize_text(column): column for column in columns}
    for candidate in candidates:
        match = normalized.get(normalize_text(candidate))
        if match:
            return match
    raise KeyError(f"missing columns: {candidates}")


def optional_column(columns: Any, *candidates: str) -> str | None:
    try:
        return pick_column(columns, *candidates)
    except KeyError:
        return None


def parse_date_value(value: Any) -> date:
    text = str(value).split(" ")[0].replace("/", "-")
    return date.fromisoformat(text)


def safe_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(str(value).replace(",", ""))


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

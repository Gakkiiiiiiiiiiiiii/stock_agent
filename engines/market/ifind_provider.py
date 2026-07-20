"""iFinD 数据接入层：财务指标获取（PIT 对齐）与低频缓存。

探测结论（2026-07-20）：当前环境未发现可用的 iFinD/同花顺 agent-gw 接口——
工具集中无 mcp__ 前缀的 iFinD/THS 工具，.env.example 与 config/*.yaml 中无
IFIND/AGENT_GW/THS 相关配置，全仓检索无 ifind/同花顺/agent-gw 痕迹。
因此数据获取层（fetch_financials）保留接口并抛 NotImplementedError；
PIT 对齐与缓存逻辑完整实现，可用模拟数据直接驱动。

PIT 口径：factor[s,d] = 披露日 ≤ d 的最新一期值（ffill）；无实际披露日时按
法定截止日近似（Q1/年报 4-30、半年报 8-31、Q3 10-31，年报为次年 4-30）。
盈利预测类字段标记为 realtime_only，只进打分层，不做 PIT 对齐进因子面板。
"""
from __future__ import annotations

import csv
import hashlib
import logging
import os
from datetime import date
from pathlib import Path

import numpy as np

from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

CACHE_DIR = "storage/cache/ifind"
DEFAULT_CACHE_TTL_DAYS = 7

# 盈利预测类字段：分析师预期是实时变化的，无"披露日"概念，
# 做 PIT 对齐反而制造假象，只进打分层（realtime_only）。
REALTIME_ONLY_FIELDS = frozenset({
    "eps_forecast", "net_profit_forecast", "revenue_forecast",
    "target_price", "analyst_rating", "forecast_count",
})


def _norm_date(value: str) -> str:
    """接受 YYYYMMDD / YYYY-MM-DD，统一返回 YYYY-MM-DD。"""
    text = str(value).strip().replace("/", "-")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def statutory_deadline(period_end: str) -> str:
    """报告期对应的法定披露截止日近似：Q1/年报 4-30（年报次年）、半年报 8-31、Q3 10-31。"""
    d = date.fromisoformat(_norm_date(period_end))
    md = f"{d.month:02d}-{d.day:02d}"
    if md == "03-31":
        return f"{d.year}-04-30"
    if md == "06-30":
        return f"{d.year}-08-31"
    if md == "09-30":
        return f"{d.year}-10-31"
    if md == "12-31":
        return f"{d.year + 1}-04-30"
    # 非标准期末：保守按 4 个月后披露
    month = d.month + 4
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year}-{month:02d}-{d.day:02d}"


def disclosure_date(record: dict) -> str:
    """记录的实际可用日：有 announce_date 用之，否则按法定截止日近似。"""
    announce = str(record.get("announce_date") or "").strip()
    return _norm_date(announce) if announce else statutory_deadline(record["period_end"])


def align_pit(
    records: list[dict],
    fields: list[str],
    dates: list[str],
    symbols: list[str],
) -> dict[str, np.ndarray]:
    """财务记录 PIT 对齐成 (n_symbols, n_days) 面板。

    records 元素需含 symbol / period_end，可选 announce_date 与各因子字段。
    realtime_only 字段被跳过（不在输出中）；每个交易日取披露日 ≤ 当日的最新一期，ffill。
    """
    pit_fields = [f for f in fields if f not in REALTIME_ONLY_FIELDS]
    panels = {f: np.full((len(symbols), len(dates)), np.nan) for f in pit_fields}
    day_dates = [_norm_date(d) for d in dates]

    by_symbol: dict[str, list[dict]] = {}
    for rec in records:
        symbol = str(rec.get("symbol") or "").strip()
        if symbol:
            by_symbol.setdefault(symbol, []).append(rec)

    for si, symbol in enumerate(symbols):
        # 兼容 records 用纯数字代码、symbols 带交易所后缀的情况
        recs = by_symbol.get(symbol) or by_symbol.get(symbol.split(".")[0]) or []
        entries = sorted(
            ((disclosure_date(r), _norm_date(str(r.get("period_end") or "")), r) for r in recs),
            key=lambda item: (item[0], item[1]),
        )
        if not entries:
            continue
        latest: tuple | None = None
        ei = 0
        for di, d in enumerate(day_dates):
            while ei < len(entries) and entries[ei][0] <= d:
                # 同一披露日可能挂多期，取期末最新的一期
                if latest is None or entries[ei][1] >= latest[1]:
                    latest = entries[ei]
                ei += 1
            if latest is None:
                continue
            rec = latest[2]
            for f in pit_fields:
                value = rec.get(f)
                if value is None:
                    continue
                try:
                    panels[f][si, di] = float(value)
                except (TypeError, ValueError):
                    continue
    return panels


class IFindProvider:
    """iFinD 财务数据提供方：低频缓存 + PIT 对齐入口。"""

    def __init__(self, cache_dir: str | Path | None = None, cache_ttl_days: int | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else project_root() / CACHE_DIR
        self.cache_ttl_days = cache_ttl_days if cache_ttl_days is not None else int(
            os.getenv("IFIND_CACHE_TTL_DAYS", DEFAULT_CACHE_TTL_DAYS))

    def fetch_financials(self, symbols: list[str], start: str, end: str) -> list[dict]:
        """拉取财务原始记录（symbol/period_end/announce_date/各字段）。

        未接入：探测未发现可用的 iFinD/agent-gw 凭据。接入点示例：
        iFinDPy.THS_iFinDLogin + THS_BD（数据池）或同花顺 agent-gw HTTP 接口，
        返回 list[dict] 后缓存与 PIT 对齐可直接复用。
        """
        raise NotImplementedError(
            "iFinD 数据接口未接入（未发现 IFIND/AGENT_GW/THS 凭据），"
            "请在 fetch_financials 中实现 iFinDPy 或 agent-gw 调用"
        )

    def _cache_path(self, symbols: list[str], start: str, end: str) -> Path:
        key = hashlib.md5(
            ("|".join(sorted(symbols)) + f"@{start}~{end}").encode("utf-8")
        ).hexdigest()[:16]
        return self.cache_dir / f"financials_{key}.csv"

    def _cache_fresh(self, path: Path) -> bool:
        age_days = (date.today() - date.fromtimestamp(path.stat().st_mtime)).days
        return age_days < self.cache_ttl_days

    @staticmethod
    def _read_cache(path: Path) -> list[dict]:
        with path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        records = []
        for row in rows:
            rec = {}
            for key, value in row.items():
                if value is None or value == "":
                    continue
                # 标识列保持字符串（纯数字代码不能被转成浮点），其余尝试数值化
                if key in ("symbol", "period_end", "announce_date"):
                    rec[key] = value
                    continue
                try:
                    rec[key] = float(value)
                except ValueError:
                    rec[key] = value
            records.append(rec)
        return records

    @staticmethod
    def _write_cache(path: Path, records: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = ["symbol", "period_end", "announce_date"]
        extra = sorted({k for rec in records for k in rec} - set(columns))
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns + extra)
            writer.writeheader()
            for rec in records:
                writer.writerow({k: rec.get(k, "") for k in columns + extra})

    def get_financials(
        self, symbols: list[str], start: str, end: str
    ) -> tuple[list[dict], str | None]:
        """取财务记录：新鲜缓存直接命中；否则拉取并写缓存。

        接口未接入（NotImplementedError）时降级：有过期缓存用缓存并附 warning，
        否则返回空列表 + warning，不抛异常（与 engines/market 惯例一致）。
        """
        path = self._cache_path(symbols, start, end)
        if path.exists() and self._cache_fresh(path):
            return self._read_cache(path), None
        try:
            records = self.fetch_financials(symbols, start, end)
        except NotImplementedError as exc:
            if path.exists():
                return self._read_cache(path), f"{exc}；已回退使用过期缓存 {path.name}"
            return [], str(exc)
        self._write_cache(path, records)
        return records, None

    def get_fundamental_panel(
        self,
        symbols: list[str],
        dates: list[str],
        fields: list[str],
    ) -> tuple[dict[str, np.ndarray], str | None]:
        """财务字段 PIT 对齐面板；realtime_only 字段不入面板（只进打分层）。"""
        records, warning = self.get_financials(symbols, dates[0], dates[-1])
        panels = align_pit(records, fields, dates, symbols)
        return panels, warning


__all__ = [
    "IFindProvider", "align_pit", "statutory_deadline", "disclosure_date",
    "REALTIME_ONLY_FIELDS",
]

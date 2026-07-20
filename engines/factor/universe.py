"""全 A 股票池构建：QMT 沪深A股 + 可交易性过滤。

过滤规则（文档 §2.1）：
- 剔除 ST / *ST / 退市标记；
- 剔除上市不满 60 个自然日；
- 剔除近 5 日无 K 线或零成交（停牌）；
- 流动性：近 20 日日均成交额 ≥ 5000 万。

CLI: python -m engines.factor.universe
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime

import yaml

from engines.market.data_provider import batched
from engines.market.qmt_bridge_client import QmtBridgeClient
from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

MIN_LISTED_DAYS = 60
MIN_AVG_AMOUNT = 5e7          # 近 20 日日均成交额门槛（元）
LIQUIDITY_LOOKBACK_DAYS = 40  # 多取一些自然日保证凑满 20 个交易日
SUSPENSION_CHECK_DAYS = 5
UNIVERSE_PATH = "config/factor_universe.yaml"
_BATCH = 200


def _fetch_all_a_share(bridge: QmtBridgeClient) -> list[str]:
    payload = bridge._run("sector-members", "--sector-name", "沪深A股", timeout_seconds=300)
    return sorted(payload.get("symbols") or [])


def _fetch_instrument_details(bridge: QmtBridgeClient, symbols: list[str]) -> dict[str, dict]:
    details: dict[str, dict] = {}
    for chunk in batched(symbols, _BATCH):
        payload = bridge._run("instrument-detail", "--symbols", ",".join(chunk), timeout_seconds=300)
        details.update(payload.get("details") or {})
    return details


def _basic_filter(symbols: list[str], details: dict[str, dict]) -> tuple[list[str], dict[str, int]]:
    """名称与上市日期过滤，返回 (保留列表, 剔除原因计数)。"""
    kept: list[str] = []
    stats = defaultdict(int)
    today = date.today()
    for symbol in symbols:
        det = details.get(symbol) or {}
        name = str(det.get("InstrumentName") or "")
        if "ST" in name.upper() or "退" in name:
            stats["st_or_delisting"] += 1
            continue
        open_date = str(det.get("OpenDate") or "")
        if open_date and len(open_date) == 8 and open_date.isdigit():
            listed = date(int(open_date[:4]), int(open_date[4:6]), int(open_date[6:8]))
            if (today - listed).days < MIN_LISTED_DAYS:
                stats["new_listing"] += 1
                continue
        kept.append(symbol)
    return kept, dict(stats)


def _liquidity_filter(bridge: QmtBridgeClient, symbols: list[str]) -> tuple[list[str], dict[str, int]]:
    """近 20 日日均成交额 + 停牌过滤。分批量拉 40 自然日日线。"""
    from datetime import timedelta

    start = (date.today() - timedelta(days=LIQUIDITY_LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    kept: list[str] = []
    stats = defaultdict(int)
    for chunk in batched(symbols, _BATCH):
        payload = bridge._run(
            "history", "--symbols", ",".join(chunk), "--period", "1d",
            "--start-time", start, "--end-time", end,
            "--dividend-type", "none", "--fill-data", "false",
            "--prefer-cache-first", "false", timeout_seconds=600,
        )
        rows_by_symbol: dict[str, list[dict]] = defaultdict(list)
        for row in payload.get("rows") or []:
            rows_by_symbol[str(row.get("symbol"))].append(row)
        for symbol in chunk:
            rows = sorted(rows_by_symbol.get(symbol) or [],
                          key=lambda r: str(r.get("trading_date") or ""))
            if len(rows) < SUSPENSION_CHECK_DAYS:
                stats["suspended_or_no_data"] += 1
                continue
            recent = rows[-SUSPENSION_CHECK_DAYS:]
            if all(float(r.get("volume") or 0) <= 0 for r in recent):
                stats["suspended_or_no_data"] += 1
                continue
            window = rows[-20:]
            avg_amount = sum(float(r.get("amount") or 0) for r in window) / max(len(window), 1)
            if avg_amount < MIN_AVG_AMOUNT:
                stats["illiquid"] += 1
                continue
            kept.append(symbol)
    return kept, dict(stats)


def build_universe(write: bool = True) -> tuple[list[str], dict]:
    bridge = QmtBridgeClient()
    all_symbols = _fetch_all_a_share(bridge)
    logger.info("全 A 原始数量: %d", len(all_symbols))

    details = _fetch_instrument_details(bridge, all_symbols)
    kept, basic_stats = _basic_filter(all_symbols, details)
    logger.info("基础过滤后: %d (%s)", len(kept), basic_stats)

    kept, liq_stats = _liquidity_filter(bridge, kept)
    logger.info("流动性过滤后: %d (%s)", len(kept), liq_stats)

    stats = {"total": len(all_symbols), "kept": len(kept), **basic_stats, **liq_stats}
    if write:
        path = project_root() / UNIVERSE_PATH
        header = (
            f"# 全 A 股票池（{datetime.now():%Y-%m-%d %H:%M} 由 engines.factor.universe 生成）\n"
            f"# 过滤: ST/退市 {basic_stats.get('st_or_delisting', 0)}, "
            f"次新 {basic_stats.get('new_listing', 0)}, "
            f"停牌/无数据 {liq_stats.get('suspended_or_no_data', 0)}, "
            f"低流动性 {liq_stats.get('illiquid', 0)}; 原始 {len(all_symbols)} -> 保留 {len(kept)}\n"
        )
        body = yaml.safe_dump({"symbols": kept}, allow_unicode=True, sort_keys=False)
        path.write_text(header + body, encoding="utf-8")
        logger.info("已写入 %s", path)
    return kept, stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    symbols, stats = build_universe()
    print("stats:", stats)

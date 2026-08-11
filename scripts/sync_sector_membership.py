"""同步 QMT 行业映射到 symbol_sector_membership 表。

用法：.venv/Scripts/python.exe scripts/sync_sector_membership.py

逻辑：拉取 bridge.get_industry_map 全量行业映射，对每个 symbol upsert
SymbolSectorMembership（source="qmt", valid_from=今天）；若 symbol 已有
未关闭的 membership 且板块发生变化，则先将旧记录 valid_to 关闭到昨天，
再写入新记录。无 QMT 桥接环境会优雅失败（退出码 1）。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines.market.data_provider import QmtMarketDataProvider  # noqa: E402
from engines.market.qmt_bridge_client import QmtBridgeError  # noqa: E402
from storage.repositories.market_feature_repository import MarketFeatureRepository  # noqa: E402

SOURCE = "qmt"


def main() -> int:
    provider = QmtMarketDataProvider()
    repository = MarketFeatureRepository()
    try:
        rows = provider.bridge.get_industry_map(symbols=[], sector_prefix="GICS2", only_a_share=True)
    except QmtBridgeError as exc:
        print(f"QMT bridge 不可用，跳过同步：{exc}")
        return 1
    today = date.today()
    yesterday = today - timedelta(days=1)

    latest: dict[str, dict[str, str]] = {}
    for row in rows or []:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        sector_code = str(row.get("industry_code") or "").strip() or "UNKNOWN"
        sector_name = str(row.get("industry_name") or "").strip() or sector_code
        latest[symbol] = {"sector_code": sector_code, "sector_name": sector_name}
    if not latest:
        print("行业映射为空，未做任何变更。")
        return 0

    try:
        existing = repository.get_memberships_at(at_date=today)
    except Exception as exc:  # noqa: BLE001 - 表不存在等场景给出明确提示
        print(f"读取现有 membership 失败（是否已执行建表/迁移？）：{exc}")
        return 1
    open_by_symbol = {membership.symbol: membership for membership in existing}

    inserted = closed = unchanged = 0
    for symbol, sector in sorted(latest.items()):
        current = open_by_symbol.get(symbol)
        if current is not None and (current.sector_code, current.sector_name) == (sector["sector_code"], sector["sector_name"]):
            unchanged += 1
            continue
        if current is not None:
            repository.upsert_membership(
                symbol=current.symbol,
                sector_code=current.sector_code,
                sector_name=current.sector_name,
                valid_from=current.valid_from,
                source=current.source,
                valid_to=yesterday,
            )
            closed += 1
        repository.upsert_membership(
            symbol=symbol,
            sector_code=sector["sector_code"],
            sector_name=sector["sector_name"],
            valid_from=today,
            source=SOURCE,
        )
        inserted += 1
    print(f"同步完成：新增/变更 {inserted}，关闭旧记录 {closed}，未变化 {unchanged}，总计 {len(latest)}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

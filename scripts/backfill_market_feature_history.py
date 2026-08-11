"""Build deterministic market and sector snapshots from historical inputs."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time

from engines.market.data_provider import QmtMarketDataProvider
from engines.market.exchange_calendar import ExchangeTradingCalendar
from engines.market.feature_service import MarketFeatureService, SectorFeatureService
from storage.bootstrap import create_all
from storage.repositories.market_feature_repository import MarketFeatureRepository


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=_parse_date)
    parser.add_argument("--end", required=True, type=_parse_date)
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args(argv)
    if args.end < args.start:
        parser.error("--end must not precede --start")
    create_all()
    repository = MarketFeatureRepository()
    provider = QmtMarketDataProvider()
    provider._feature_repository = repository  # point the historical breadth path at the same PIT store
    market_service = MarketFeatureService(provider=provider, repository=repository)
    sector_service = SectorFeatureService(provider=provider, repository=repository)
    calendar = ExchangeTradingCalendar()
    day, rebuilt, failed = args.start, 0, []
    while day <= args.end:
        if calendar.is_trading_day(day):
            as_of = datetime.combine(day, time(15, 0))
            sectors = sector_service.get_sector_strength(top_k=args.top_k, as_of=as_of, read_cache=False)
            result = market_service.get_market_features(as_of=as_of)
            flags = set((result.get("meta") or {}).get("quality_flags") or [])
            if not sectors or "HISTORICAL_MEMBERSHIP_UNAVAILABLE" in flags:
                failed.append(day.isoformat())
            else:
                rebuilt += 1
        day = date.fromordinal(day.toordinal() + 1)
    print(json.dumps({"rebuilt": rebuilt, "failed_dates": failed, "failed_count": len(failed)}, ensure_ascii=False))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())

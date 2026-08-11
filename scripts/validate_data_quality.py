"""Report persisted market/sector feature quality flags for a PIT window."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date

from sqlalchemy import select

from storage.db import session_scope
from storage.models.market_feature import MarketFeatureSnapshot, SectorFeatureSnapshot


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def validate(start: date, end: date) -> dict:
    with session_scope() as session:
        market = list(session.execute(select(MarketFeatureSnapshot).where(MarketFeatureSnapshot.trade_date >= start, MarketFeatureSnapshot.trade_date <= end)).scalars())
        sector = list(session.execute(select(SectorFeatureSnapshot).where(SectorFeatureSnapshot.trade_date >= start, SectorFeatureSnapshot.trade_date <= end)).scalars())
    flags = Counter(flag for item in [*market, *sector] for flag in (item.quality_flags or []))
    critical = sorted(flag for flag in flags if any(token in str(flag).upper() for token in ("UNAVAILABLE", "INSUFFICIENT", "INCOMPLETE", "FUTURE_DATA")))
    return {"start": start.isoformat(), "end": end.isoformat(), "market_snapshot_count": len(market), "sector_snapshot_count": len(sector), "quality_flags": dict(sorted(flags.items())), "critical_flags": critical, "passed": bool(market) and bool(sector) and not critical}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=_parse_date)
    parser.add_argument("--end", required=True, type=_parse_date)
    args = parser.parse_args(argv)
    report = validate(args.start, args.end)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

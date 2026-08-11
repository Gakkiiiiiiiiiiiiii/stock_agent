"""Validate persisted historical PIT coverage without rebuilding features."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

from sqlalchemy import select

from storage.db import session_scope
from storage.models.market_feature import MarketFeatureSnapshot, SectorFeatureSnapshot, SymbolSectorMembership


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def _expected_days(path: Path | None) -> set[date]:
    if path is None:
        return set()
    if path.suffix.lower() == ".json":
        values = json.loads(path.read_text(encoding="utf-8"))
        return {_parse_date(item) for item in values}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {_parse_date(row.get("date") or row.get("trading_date")) for row in csv.DictReader(handle)}


def validate(start: date, end: date, expected_days: set[date] | None = None, min_sector_coverage: float = .8) -> dict:
    with session_scope() as session:
        markets = list(session.execute(select(MarketFeatureSnapshot).where(MarketFeatureSnapshot.trade_date >= start, MarketFeatureSnapshot.trade_date <= end)).scalars())
        sectors = list(session.execute(select(SectorFeatureSnapshot).where(SectorFeatureSnapshot.trade_date >= start, SectorFeatureSnapshot.trade_date <= end)).scalars())
        memberships = list(session.execute(select(SymbolSectorMembership).where(SymbolSectorMembership.valid_from <= end, (SymbolSectorMembership.valid_to.is_(None)) | (SymbolSectorMembership.valid_to >= start))).scalars())
    market_by_day = {item.trade_date: item for item in markets}
    sector_by_day: dict[date, list] = {}
    for item in sectors:
        sector_by_day.setdefault(item.trade_date, []).append(item)
    target_days = expected_days or set(market_by_day)
    failed_dates = []
    coverage_values = []
    for day in sorted(target_days):
        rows = sector_by_day.get(day, [])
        coverage = min((float(item.coverage) for item in rows), default=0.0)
        coverage_values.append(coverage)
        has_membership = any(item.valid_from <= day and (item.valid_to is None or item.valid_to >= day) for item in memberships)
        if day not in market_by_day or not rows or not has_membership or coverage < min_sector_coverage:
            failed_dates.append(day.isoformat())
    future_violations = [
        {"kind": "market", "trade_date": item.trade_date.isoformat(), "as_of": item.as_of.isoformat()}
        for item in markets if item.as_of.date() > item.trade_date
    ] + [
        {"kind": "sector", "trade_date": item.trade_date.isoformat(), "as_of": item.as_of.isoformat()}
        for item in sectors if item.as_of.date() > item.trade_date
    ]
    return {
        "start": start.isoformat(), "end": end.isoformat(), "trading_days": len(target_days), "snapshot_count": len(markets),
        "sector_snapshot_count": len(sectors), "membership_coverage": len({item.symbol for item in memberships}),
        "sector_coverage": sum(coverage_values) / len(coverage_values) if coverage_values else 0.0,
        "failed_dates": failed_dates, "future_data_violation_count": len(future_violations), "future_data_violations": future_violations,
        "passed": bool(target_days) and not failed_dates and not future_violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=_parse_date)
    parser.add_argument("--end", required=True, type=_parse_date)
    parser.add_argument("--expected-trading-days", type=Path, help="optional CSV/JSON authoritative exchange calendar")
    parser.add_argument("--min-sector-coverage", type=float, default=.8)
    args = parser.parse_args(argv)
    report = validate(args.start, args.end, _expected_days(args.expected_trading_days), args.min_sector_coverage)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

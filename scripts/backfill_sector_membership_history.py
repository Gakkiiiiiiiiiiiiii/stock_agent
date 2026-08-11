"""Backfill point-in-time sector membership without silently using today\'s map.

The input must contain historical observations (JSONL or CSV) with ``date``,
``symbol``, ``sector_code`` and ``sector_name``.  A current QMT mapping is
intentionally not accepted as a historical substitute.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

from storage.bootstrap import create_all
from storage.repositories.market_feature_repository import MarketFeatureRepository


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def _read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def backfill(rows: list[dict], start: date, end: date, repository: MarketFeatureRepository) -> dict:
    grouped: dict[str, list[tuple[date, dict]]] = {}
    for row in rows:
        observed = _parse_date(row["date"])
        if not start <= observed <= end:
            continue
        source = str(row.get("source") or "HISTORICAL_MEMBERSHIP_BACKFILL")
        if source == "CURRENT_MEMBERSHIP_BACKFILL" or str(row.get("point_in_time_valid", "true")).lower() in {"0", "false", "no"}:
            raise ValueError("refusing non-point-in-time membership backfill")
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            grouped.setdefault(symbol, []).append((observed, row))
    written = 0
    for symbol, observations in grouped.items():
        observations.sort(key=lambda item: item[0])
        for index, (valid_from, row) in enumerate(observations):
            next_from = observations[index + 1][0] if index + 1 < len(observations) else None
            repository.upsert_membership(
                symbol=symbol,
                sector_code=str(row.get("sector_code") or "UNKNOWN"),
                sector_name=str(row.get("sector_name") or row.get("sector_code") or "UNKNOWN"),
                valid_from=valid_from,
                valid_to=next_from - timedelta(days=1) if next_from else None,
                source=str(row.get("source") or "HISTORICAL_MEMBERSHIP_BACKFILL"),
            )
            written += 1
    return {"symbols": len(grouped), "memberships_written": written, "point_in_time_valid": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=_parse_date)
    parser.add_argument("--end", required=True, type=_parse_date)
    parser.add_argument("--input", required=True, type=Path, help="historical JSONL/CSV membership export")
    args = parser.parse_args(argv)
    if args.end < args.start:
        parser.error("--end must not precede --start")
    create_all()
    result = backfill(_read_rows(args.input), args.start, args.end, MarketFeatureRepository())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

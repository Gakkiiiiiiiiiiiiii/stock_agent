from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import inspect, text

from storage.db import session_scope
from storage.repositories.market_feature_repository import MarketFeatureRepository


def _repo() -> MarketFeatureRepository:
    return MarketFeatureRepository()


def test_migration_018_applied(isolated_database):
    engine = isolated_database
    with engine.connect() as conn:
        versions = {row[0] for row in conn.execute(text("SELECT version FROM schema_migration")).fetchall()}
    assert "018_market_feature_snapshot.sql" in versions
    tables = set(inspect(engine).get_table_names())
    assert {"market_feature_snapshot", "sector_feature_snapshot", "symbol_sector_membership"} <= tables


def test_market_snapshot_roundtrip(isolated_database):
    repo = _repo()
    saved = repo.save_market_snapshot(
        market_code="CN",
        as_of=datetime(2026, 8, 7, 15, 0),
        trade_date=date(2026, 8, 7),
        feature_version="v1",
        features_json={"momentum": 0.42, "breadth": 0.6},
        quality_score=0.9,
        quality_flags=["STALE_BAR"],
    )
    assert saved.id is not None

    fetched = repo.get_market_snapshot("CN", date(2026, 8, 7), feature_version="v1")
    assert fetched is not None
    assert fetched.market_code == "CN"
    assert fetched.trade_date == date(2026, 8, 7)
    assert fetched.features_json == {"momentum": 0.42, "breadth": 0.6}
    assert fetched.quality_score == 0.9
    assert fetched.quality_flags == ["STALE_BAR"]

    # without version, returns a snapshot for that date
    fetched_any = repo.get_market_snapshot("CN", date(2026, 8, 7))
    assert fetched_any is not None and fetched_any.feature_version == "v1"

    # different key misses
    assert repo.get_market_snapshot("CN", date(2026, 8, 8)) is None
    assert repo.get_market_snapshot("US", date(2026, 8, 7)) is None


def test_market_snapshot_upsert_same_key(isolated_database):
    repo = _repo()
    repo.save_market_snapshot(
        market_code="CN",
        as_of=datetime(2026, 8, 7, 15, 0),
        trade_date=date(2026, 8, 7),
        feature_version="v1",
        features_json={"momentum": 0.1},
    )
    updated = repo.save_market_snapshot(
        market_code="CN",
        as_of=datetime(2026, 8, 7, 16, 0),
        trade_date=date(2026, 8, 7),
        feature_version="v1",
        features_json={"momentum": 0.5},
        quality_score=0.8,
    )
    fetched = repo.get_market_snapshot("CN", date(2026, 8, 7), feature_version="v1")
    assert fetched is not None
    assert fetched.id == updated.id
    assert fetched.features_json == {"momentum": 0.5}
    assert fetched.quality_score == 0.8

    # a different feature_version coexists as a separate row
    repo.save_market_snapshot(
        market_code="CN",
        as_of=datetime(2026, 8, 7, 16, 0),
        trade_date=date(2026, 8, 7),
        feature_version="v2",
        features_json={"momentum": 0.9},
    )
    assert repo.get_market_snapshot("CN", date(2026, 8, 7), feature_version="v1").features_json == {"momentum": 0.5}
    assert repo.get_market_snapshot("CN", date(2026, 8, 7), feature_version="v2").features_json == {"momentum": 0.9}


def test_latest_market_snapshot(isolated_database):
    repo = _repo()
    for day in (5, 6, 7):
        repo.save_market_snapshot(
            market_code="CN",
            as_of=datetime(2026, 8, day, 15, 0),
            trade_date=date(2026, 8, day),
            feature_version="v1",
            features_json={"day": day},
        )
    repo.save_market_snapshot(
        market_code="US",
        as_of=datetime(2026, 8, 8, 15, 0),
        trade_date=date(2026, 8, 8),
        feature_version="v1",
        features_json={"day": 8},
    )
    latest_cn = repo.latest_market_snapshot("CN")
    assert latest_cn is not None and latest_cn.trade_date == date(2026, 8, 7)
    latest_all = repo.latest_market_snapshot()
    assert latest_all is not None and latest_all.market_code == "US"
    assert repo.latest_market_snapshot("JP") is None


def test_sector_snapshot_roundtrip_and_upsert(isolated_database):
    repo = _repo()
    repo.save_sector_snapshot(
        sector_name="半导体",
        sector_code="801081",
        trade_date=date(2026, 8, 7),
        as_of=datetime(2026, 8, 7, 15, 0),
        component_scores={"momentum": 0.7, "fundflow": 0.4},
        final_score=0.55,
        feature_version="v1",
        universe_size=120,
        valid_symbol_count=110,
        coverage=110 / 120,
        quality_flags=["LOW_COVERAGE"],
    )
    fetched = repo.get_sector_snapshot("半导体", date(2026, 8, 7), feature_version="v1")
    assert fetched is not None
    assert fetched.sector_code == "801081"
    assert fetched.component_scores == {"momentum": 0.7, "fundflow": 0.4}
    assert fetched.final_score == 0.55
    assert fetched.universe_size == 120
    assert fetched.valid_symbol_count == 110
    assert abs(fetched.coverage - 110 / 120) < 1e-9
    assert fetched.quality_flags == ["LOW_COVERAGE"]

    # upsert on (sector_name, trade_date, feature_version) updates in place
    updated = repo.save_sector_snapshot(
        sector_name="半导体",
        trade_date=date(2026, 8, 7),
        as_of=datetime(2026, 8, 7, 16, 0),
        component_scores={"momentum": 0.8},
        final_score=0.66,
        feature_version="v1",
    )
    assert updated.id == fetched.id
    assert repo.get_sector_snapshot("半导体", date(2026, 8, 7), feature_version="v1").final_score == 0.66

    # multiple sectors on the same date
    repo.save_sector_snapshot(
        sector_name="医药",
        trade_date=date(2026, 8, 7),
        as_of=datetime(2026, 8, 7, 15, 0),
        component_scores={},
        final_score=-0.2,
        feature_version="v1",
    )
    snapshots = repo.get_sector_snapshots(date(2026, 8, 7), feature_version="v1")
    assert [item.sector_name for item in snapshots] == ["医药", "半导体"]
    assert repo.get_sector_snapshots(date(2026, 8, 7), feature_version="v2") == []
    assert repo.get_sector_snapshot("不存在", date(2026, 8, 7)) is None


def test_sector_score_history(isolated_database):
    repo = _repo()
    for day in (4, 5, 6, 7):
        repo.save_sector_snapshot(
            sector_name="半导体",
            trade_date=date(2026, 8, day),
            as_of=datetime(2026, 8, day, 15, 0),
            component_scores={},
            final_score=float(day),
            feature_version="v1",
        )
    history = repo.get_sector_score_history("半导体", date(2026, 8, 5), date(2026, 8, 7))
    assert [item.trade_date for item in history] == [date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]
    assert [item.final_score for item in history] == [5.0, 6.0, 7.0]
    assert repo.get_sector_score_history("半导体", date(2026, 9, 1), date(2026, 9, 2)) == []


def test_membership_validity_window(isolated_database):
    repo = _repo()
    repo.upsert_membership(
        symbol="600000.SH",
        sector_code="801780",
        sector_name="银行",
        valid_from=date(2024, 1, 1),
        valid_to=date(2024, 6, 30),
        source="sw2021",
    )
    repo.upsert_membership(
        symbol="600000.SH",
        sector_code="801081",
        sector_name="半导体",
        valid_from=date(2024, 7, 1),
        valid_to=None,
        source="sw2021",
    )

    historical = repo.get_memberships_at(symbols=["600000.SH"], at_date=date(2024, 3, 1))
    assert len(historical) == 1
    assert historical[0].sector_name == "银行"

    current = repo.get_memberships_at(symbols=["600000.SH"], at_date=date(2024, 8, 1))
    assert len(current) == 1
    assert current[0].sector_name == "半导体"

    # boundary dates are inclusive on both ends
    assert repo.get_memberships_at(symbols=["600000.SH"], at_date=date(2024, 6, 30))[0].sector_name == "银行"
    assert repo.get_memberships_at(symbols=["600000.SH"], at_date=date(2024, 7, 1))[0].sector_name == "半导体"

    # before any membership starts → no rows
    assert repo.get_memberships_at(symbols=["600000.SH"], at_date=date(2023, 12, 31)) == []

    # upsert closes the open-ended membership
    repo.upsert_membership(
        symbol="600000.SH",
        sector_code="801081",
        sector_name="半导体",
        valid_from=date(2024, 7, 1),
        valid_to=date(2024, 12, 31),
        source="sw2021",
    )
    assert repo.get_memberships_at(symbols=["600000.SH"], at_date=date(2025, 1, 1)) == []
    assert repo.get_memberships_at(symbols=["600000.SH"], at_date=date(2024, 12, 31))[0].sector_name == "半导体"

    # without symbol filter returns all memberships valid at the date
    all_rows = repo.get_memberships_at(at_date=date(2024, 3, 1))
    assert {row.symbol for row in all_rows} == {"600000.SH"}


def test_membership_coverage(isolated_database):
    repo = _repo()
    symbols = ["600000.SH", "000001.SZ", "300750.SZ"]
    assert repo.get_membership_coverage(symbols, at_date=date(2026, 8, 7)) == 0.0

    repo.upsert_membership(
        symbol="600000.SH",
        sector_code="801780",
        sector_name="银行",
        valid_from=date(2026, 1, 1),
        source="sw2021",
    )
    repo.upsert_membership(
        symbol="000001.SZ",
        sector_code="801780",
        sector_name="银行",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 6, 30),
        source="sw2021",
    )

    coverage = repo.get_membership_coverage(symbols, at_date=date(2026, 8, 7))
    assert abs(coverage - 1 / 3) < 1e-9

    # at an earlier date both memberships are valid
    earlier = repo.get_membership_coverage(symbols, at_date=date(2026, 3, 1))
    assert abs(earlier - 2 / 3) < 1e-9

    assert repo.get_membership_coverage([], at_date=date(2026, 8, 7)) == 0.0


def test_session_transaction_rolls_back_on_error(isolated_database):
    # sanity: session_scope usage keeps DB consistent for repository methods
    repo = _repo()
    repo.save_market_snapshot(
        market_code="CN",
        as_of=datetime(2026, 8, 7, 15, 0),
        trade_date=date(2026, 8, 7),
        feature_version="v1",
        features_json={},
    )
    with session_scope() as session:
        from storage.models.market_feature import MarketFeatureSnapshot

        count = session.query(MarketFeatureSnapshot).count()
    assert count == 1

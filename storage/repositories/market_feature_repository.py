from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Sequence

from sqlalchemy import func, select

from storage.db import session_scope
from storage.models.market_feature import (
    MarketFeatureSnapshot,
    SectorFeatureSnapshot,
    SymbolSectorMembership,
)


class MarketFeatureRepository:
    def save_market_snapshot(
        self,
        market_code: str,
        as_of: datetime,
        trade_date: date,
        feature_version: str,
        features_json: dict,
        quality_score: float | None = None,
        quality_flags: list | None = None,
    ) -> MarketFeatureSnapshot:
        with session_scope() as session:
            snapshot = session.execute(
                select(MarketFeatureSnapshot).where(
                    MarketFeatureSnapshot.market_code == market_code,
                    MarketFeatureSnapshot.trade_date == trade_date,
                    MarketFeatureSnapshot.feature_version == feature_version,
                )
            ).scalars().first()
            if snapshot is None:
                snapshot = MarketFeatureSnapshot(
                    market_code=market_code,
                    trade_date=trade_date,
                    feature_version=feature_version,
                )
                session.add(snapshot)
            snapshot.as_of = as_of
            snapshot.features_json = features_json
            snapshot.quality_score = quality_score
            snapshot.quality_flags = quality_flags if quality_flags is not None else []
            session.flush()
            session.refresh(snapshot)
            return snapshot

    def get_market_snapshot(
        self,
        market_code: str,
        trade_date: date,
        feature_version: str | None = None,
    ) -> MarketFeatureSnapshot | None:
        with session_scope() as session:
            query = select(MarketFeatureSnapshot).where(
                MarketFeatureSnapshot.market_code == market_code,
                MarketFeatureSnapshot.trade_date == trade_date,
            )
            if feature_version is not None:
                query = query.where(MarketFeatureSnapshot.feature_version == feature_version)
            return session.execute(query.order_by(MarketFeatureSnapshot.id.desc())).scalars().first()

    def latest_market_snapshot(self, market_code: str | None = None) -> MarketFeatureSnapshot | None:
        with session_scope() as session:
            query = select(MarketFeatureSnapshot)
            if market_code is not None:
                query = query.where(MarketFeatureSnapshot.market_code == market_code)
            return session.execute(
                query.order_by(MarketFeatureSnapshot.trade_date.desc(), MarketFeatureSnapshot.id.desc())
            ).scalars().first()

    def save_sector_snapshot(
        self,
        sector_name: str,
        trade_date: date,
        as_of: datetime,
        component_scores: dict,
        final_score: float,
        feature_version: str,
        sector_code: str | None = None,
        universe_size: int = 0,
        valid_symbol_count: int = 0,
        coverage: float = 0.0,
        quality_flags: list | None = None,
    ) -> SectorFeatureSnapshot:
        with session_scope() as session:
            snapshot = session.execute(
                select(SectorFeatureSnapshot).where(
                    SectorFeatureSnapshot.sector_name == sector_name,
                    SectorFeatureSnapshot.trade_date == trade_date,
                    SectorFeatureSnapshot.feature_version == feature_version,
                )
            ).scalars().first()
            if snapshot is None:
                snapshot = SectorFeatureSnapshot(
                    sector_name=sector_name,
                    trade_date=trade_date,
                    feature_version=feature_version,
                )
                session.add(snapshot)
            snapshot.sector_code = sector_code
            snapshot.as_of = as_of
            snapshot.component_scores = component_scores
            snapshot.final_score = final_score
            snapshot.universe_size = universe_size
            snapshot.valid_symbol_count = valid_symbol_count
            snapshot.coverage = coverage
            snapshot.quality_flags = quality_flags if quality_flags is not None else []
            session.flush()
            session.refresh(snapshot)
            return snapshot

    def get_sector_snapshots(
        self,
        trade_date: date,
        feature_version: str | None = None,
    ) -> list[SectorFeatureSnapshot]:
        with session_scope() as session:
            query = select(SectorFeatureSnapshot).where(SectorFeatureSnapshot.trade_date == trade_date)
            if feature_version is not None:
                query = query.where(SectorFeatureSnapshot.feature_version == feature_version)
            return list(session.execute(query.order_by(SectorFeatureSnapshot.sector_name)).scalars())

    def get_sector_snapshot(
        self,
        sector_name: str,
        trade_date: date,
        feature_version: str | None = None,
    ) -> SectorFeatureSnapshot | None:
        with session_scope() as session:
            query = select(SectorFeatureSnapshot).where(
                SectorFeatureSnapshot.sector_name == sector_name,
                SectorFeatureSnapshot.trade_date == trade_date,
            )
            if feature_version is not None:
                query = query.where(SectorFeatureSnapshot.feature_version == feature_version)
            return session.execute(query.order_by(SectorFeatureSnapshot.id.desc())).scalars().first()

    def get_sector_score_history(
        self,
        sector_name: str,
        start_date: date,
        end_date: date,
        feature_version: str | None = None,
    ) -> list[SectorFeatureSnapshot]:
        with session_scope() as session:
            query = select(SectorFeatureSnapshot).where(
                SectorFeatureSnapshot.sector_name == sector_name,
                SectorFeatureSnapshot.trade_date >= start_date,
                SectorFeatureSnapshot.trade_date <= end_date,
            )
            if feature_version is not None:
                query = query.where(SectorFeatureSnapshot.feature_version == feature_version)
            return list(session.execute(query.order_by(SectorFeatureSnapshot.trade_date)).scalars())

    def upsert_membership(
        self,
        symbol: str,
        sector_code: str,
        sector_name: str,
        valid_from: date,
        source: str,
        valid_to: date | None = None,
    ) -> SymbolSectorMembership:
        with session_scope() as session:
            membership = session.execute(
                select(SymbolSectorMembership).where(
                    SymbolSectorMembership.symbol == symbol,
                    SymbolSectorMembership.valid_from == valid_from,
                )
            ).scalars().first()
            if membership is None:
                membership = SymbolSectorMembership(symbol=symbol, valid_from=valid_from)
                session.add(membership)
            membership.sector_code = sector_code
            membership.sector_name = sector_name
            membership.source = source
            membership.valid_to = valid_to
            session.flush()
            session.refresh(membership)
            return membership

    def get_memberships_at(
        self,
        symbols: Sequence[str] | None = None,
        at_date: date | None = None,
    ) -> list[SymbolSectorMembership]:
        effective_date = at_date or date.today()
        with session_scope() as session:
            query = select(SymbolSectorMembership).where(
                SymbolSectorMembership.valid_from <= effective_date,
                (SymbolSectorMembership.valid_to.is_(None)) | (SymbolSectorMembership.valid_to >= effective_date),
            )
            if symbols:
                query = query.where(SymbolSectorMembership.symbol.in_(list(symbols)))
            return list(session.execute(query.order_by(SymbolSectorMembership.symbol)).scalars())

    def get_membership_coverage(self, symbols: Iterable[str], at_date: date | None = None) -> float:
        unique_symbols = set(symbols)
        if not unique_symbols:
            return 0.0
        effective_date = at_date or date.today()
        with session_scope() as session:
            covered = session.execute(
                select(func.count(func.distinct(SymbolSectorMembership.symbol))).where(
                    SymbolSectorMembership.symbol.in_(list(unique_symbols)),
                    SymbolSectorMembership.valid_from <= effective_date,
                    (SymbolSectorMembership.valid_to.is_(None)) | (SymbolSectorMembership.valid_to >= effective_date),
                )
            ).scalar_one()
            return covered / len(unique_symbols)

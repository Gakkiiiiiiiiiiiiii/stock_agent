"""市场/板块特征服务：组合数据提供方、特征构建器与质量门控，输出 DomainResult 风格结果。

两个服务均接受可选 repository（鸭子类型）；持久化失败绝不阻断计算。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from engines.market.data_provider import batched, get_market_data_provider, group_history_rows
from engines.market.feature_builder import MarketFeatureBuilder
from engines.market.feature_quality import DATA_QUALITY_INSUFFICIENT, evaluate_market_data_quality
from engines.market.sector_feature_builder import SectorFeatureBuilder
from engines.versioning import get_version
from financial_agent.config import load_yaml_config

logger = logging.getLogger(__name__)

MARKET_CALCULATION_VERSION = "market_feature_v2"
DEFAULT_SECTOR_CALCULATION_VERSION = "sector_strength_v2"
DEFAULT_MARKET_CODE = "CN_A"


def _provider_call(provider: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    """优先调用 provider 自身方法；缺失时回退到 provider.bridge（如 QmtBridgeClient）。"""
    fn = getattr(provider, name, None)
    if fn is None:
        fn = getattr(getattr(provider, "bridge", None), name, None)
    if fn is None:
        raise AttributeError(f"provider 缺少方法 {name}")
    return fn(*args, **kwargs)


class _BuilderDataAccess:
    """适配层：为 SectorFeatureBuilder 提供 get_quotes / get_kline 鸭子接口。

    全量成分股场景下逐标的 get_kline 会产生数千次串行 bridge 调用，
    因此支持通过 warm_kline_cache 以多标的批量 get_history 预热缓存。
    """

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self._kline_cache: dict[str, Any] = {}

    def get_quotes(self, symbols: list[str]) -> dict[str, Any]:
        return _provider_call(self.provider, "get_quotes", symbols)

    def get_kline(self, symbol: str) -> Any:
        if symbol in self._kline_cache:
            return self._kline_cache[symbol]
        return _provider_call(self.provider, "get_kline", symbol)

    def warm_kline_cache(self, symbols: list[str], batch_size: int = 200, lookback_days: int = 240, as_of: date | None = None) -> None:
        """批量预取 K 线；无批量接口（如测试假 provider）时静默跳过。"""
        fetch = getattr(self.provider, "get_history", None)
        if fetch is None:
            fetch = getattr(getattr(self.provider, "bridge", None), "get_history", None)
        if fetch is None:
            return
        pending = [symbol for symbol in symbols if symbol not in self._kline_cache]
        if not pending:
            return
        anchor = as_of or date.today()
        start = (anchor - timedelta(days=lookback_days)).strftime("%Y%m%d")
        end = anchor.strftime("%Y%m%d")
        for chunk in batched(sorted(set(pending)), batch_size):
            try:
                rows = fetch(
                    symbols=chunk,
                    period="1d",
                    start_time=start,
                    end_time=end,
                    dividend_type="front",
                    fill_data=True,
                    prefer_cache_first=True,
                )
            except Exception:  # noqa: BLE001 - 单批失败回退到逐标的获取
                continue
            grouped = group_history_rows(rows or [])
            for symbol in chunk:
                self._kline_cache[symbol] = grouped.get(symbol, [])


class MarketFeatureService:
    def __init__(self, provider: Any = None, repository: Any = None) -> None:
        self.provider = provider or get_market_data_provider()
        self.repository = repository

    def get_market_features(self, as_of: datetime | date | None = None) -> dict[str, Any]:
        feature = MarketFeatureBuilder(self.provider).build(as_of=_as_datetime(as_of))
        quality_thresholds = _load_config_section("data_quality.yaml", "data_quality")
        quality = evaluate_market_data_quality(feature, quality_thresholds)
        warnings = [feature["warning"]] if feature.get("warning") else []
        quality_flags = sorted(set(feature.get("quality_flags") or []) | set(quality["quality_flags"]))
        meta = {
            "as_of": feature.get("as_of"),
            "data_source": feature.get("source"),
            "calculation_version": get_version("market_feature_version", MARKET_CALCULATION_VERSION),
            "coverage": feature.get("quote_coverage"),
            "confidence": quality["quality_score"],
            "warnings": warnings,
            "quality_flags": quality_flags,
            "quality_status": quality["status"],
        }
        result: dict[str, Any] = {"data": feature, "meta": meta}
        if quality["status"] == "INSUFFICIENT":
            # 数据质量门控不足：在 payload 中显式标记，供 agent 层下调置信度
            meta["warnings"] = warnings + ["数据质量不足（DATA_QUALITY_INSUFFICIENT），请下调结论置信度"]
            result["status"] = DATA_QUALITY_INSUFFICIENT
        self._persist(feature, meta)
        return result

    def _persist(self, feature: dict[str, Any], meta: dict[str, Any]) -> None:
        if self.repository is None:
            return
        save = getattr(self.repository, "save_market_snapshot", None)
        if save is None:
            return
        try:
            as_of = _as_datetime(meta.get("as_of")) or datetime.now(timezone.utc)
            save(
                market_code=DEFAULT_MARKET_CODE,
                as_of=as_of,
                trade_date=_as_date(as_of) or date.today(),
                feature_version=str(meta.get("calculation_version") or MARKET_CALCULATION_VERSION),
                features_json=_jsonable(feature),
                quality_score=meta.get("confidence"),
                quality_flags=list(meta.get("quality_flags") or []),
            )
        except Exception:  # noqa: BLE001 - 持久化失败不影响计算结果
            logger.warning("save_market_snapshot failed", exc_info=True)


class SectorFeatureService:
    def __init__(self, provider: Any = None, repository: Any = None, config: dict[str, Any] | None = None) -> None:
        self.provider = provider or get_market_data_provider()
        self.repository = repository
        self.config = config or _load_config_section("sector_strength.yaml", "sector_strength")

    def get_sector_strength(
        self,
        top_k: int = 20,
        as_of: datetime | date | None = None,
        read_cache: bool = True,
    ) -> list[dict[str, Any]]:
        as_of_dt = _as_datetime(as_of)
        if read_cache:
            cached = self._read_cache(top_k=top_k, as_of=as_of_dt)
            if cached:
                return cached
        membership, sector_codes = self._load_membership(_as_date(as_of_dt))
        if not membership:
            return []
        feature_version = str(
            self.config.get("calculation_version")
            or get_version("sector_strength_version", DEFAULT_SECTOR_CALCULATION_VERSION)
        )
        builder = SectorFeatureBuilder(
            self._warm_data_access(membership, _as_date(as_of_dt)),
            low_coverage_threshold=float(self.config.get("low_coverage_threshold", 0.6)),
        )
        features = builder.build_sector_features(membership, market_context=self._market_context(_as_date(as_of_dt)), sector_codes=sector_codes)
        results = builder.compute_strength(
            features,
            weights=dict(self.config.get("weights") or {}),
            feature_version=feature_version,
            as_of=as_of_dt,
        )
        payload = [_enrich_payload(item.model_dump(mode="json"), features.get(item.sector) or {}) for item in results[:top_k]]
        self._persist(payload, as_of_dt)
        return payload

    def get_sector_features(self, sector: str, as_of: datetime | date | None = None) -> dict[str, Any] | None:
        as_of_dt = _as_datetime(as_of)
        membership, sector_codes = self._load_membership(_as_date(as_of_dt))
        if sector not in membership:
            return None
        builder = SectorFeatureBuilder(
            self._warm_data_access(membership, _as_date(as_of_dt)),
            low_coverage_threshold=float(self.config.get("low_coverage_threshold", 0.6)),
        )
        features = builder.build_sector_features(membership, market_context=self._market_context(_as_date(as_of_dt)), sector_codes=sector_codes)
        detail = dict(features[sector])
        strengths = builder.compute_strength(
            features,
            weights=dict(self.config.get("weights") or {}),
            feature_version=str(
                self.config.get("calculation_version")
                or get_version("sector_strength_version", DEFAULT_SECTOR_CALCULATION_VERSION)
            ),
            as_of=as_of_dt,
        )
        for item in strengths:
            if item.sector == sector:
                detail["strength_score"] = item.strength_score
                detail["rank"] = item.rank
                detail["components"] = item.components.model_dump()
                detail["feature_version"] = item.feature_version
                detail["quality_flags"] = item.quality_flags
                break
        detail["as_of"] = as_of_dt or datetime.now(timezone.utc)
        return detail

    def _warm_data_access(self, membership: dict[str, list[str]], as_of: date | None = None) -> _BuilderDataAccess:
        data_access = _BuilderDataAccess(self.provider)
        all_symbols = sorted({symbol for symbols in membership.values() for symbol in symbols})
        data_access.warm_kline_cache(all_symbols, as_of=as_of)
        return data_access

    def _load_membership(self, as_of: date | None = None) -> tuple[dict[str, list[str]], dict[str, str]]:
        """Load point-in-time constituents. Historical calls must never use QMT's
        current industry map because that would leak future reclassifications."""
        requested_date = as_of or date.today()
        if requested_date < date.today():
            getter = getattr(self.repository, "get_memberships_at", None)
            if getter is None:
                return {}, {}
            rows = getter(at_date=requested_date) or []
            membership: dict[str, list[str]] = {}
            sector_codes: dict[str, str] = {}
            for row in rows:
                sector = str(getattr(row, "sector_name", "") or "未分类")
                symbol = str(getattr(row, "symbol", "") or "").strip()
                if symbol:
                    membership.setdefault(sector, []).append(symbol)
                    code = str(getattr(row, "sector_code", "") or "")
                    if code:
                        sector_codes.setdefault(sector, code)
            return membership, sector_codes
        rows = _provider_call(self.provider, "get_industry_map", symbols=[], sector_prefix="GICS2", only_a_share=True)
        membership: dict[str, list[str]] = {}
        sector_codes: dict[str, str] = {}
        for row in rows or []:
            sector = str(row.get("industry_name") or row.get("industry_code") or "").strip() or "未分类"
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            membership.setdefault(sector, []).append(symbol)
            code = str(row.get("industry_code") or "").strip()
            if code and sector not in sector_codes:
                sector_codes[sector] = code
        return membership, sector_codes

    def _market_context(self, as_of: date | None = None) -> dict[str, Any]:
        context: dict[str, Any] = {}
        try:
            snapshot = self.provider.get_market_snapshot(as_of=as_of)
        except Exception:  # noqa: BLE001 - 市场上下文缺失时按空上下文继续
            return context
        indices = snapshot.get("indices") or {}
        context["market_return_5d"] = indices.get("return_5d_pct")
        context["market_return_20d"] = indices.get("return_20d_pct")
        context["total_market_amount"] = snapshot.get("turnover_amount") or snapshot.get("turnover")
        context["trade_date"] = as_of or date.today()
        return context

    def _read_cache(self, top_k: int, as_of: datetime | None) -> list[dict[str, Any]] | None:
        if self.repository is None:
            return None
        getter = getattr(self.repository, "get_sector_snapshots", None)
        if getter is None:
            return None
        trade_date = _as_date(as_of) or date.today()
        try:
            rows = getter(trade_date=trade_date, feature_version=None) or []
        except Exception:  # noqa: BLE001 - 缓存读取失败回退到实时计算
            logger.warning("get_sector_snapshots failed", exc_info=True)
            return None
        if not rows:
            return None
        rows = sorted(rows, key=lambda row: (-(float(getattr(row, "final_score", None) or 0.0)), str(getattr(row, "sector_name", ""))))
        return [_snapshot_row_to_payload(row, rank=index) for index, row in enumerate(rows[:top_k], start=1)]

    def _persist(self, payload: list[dict[str, Any]], as_of: datetime | None) -> None:
        if self.repository is None:
            return
        saver = getattr(self.repository, "save_sector_snapshot", None)
        if saver is None:
            return
        as_of_dt = as_of or datetime.now(timezone.utc)
        trade_date = _as_date(as_of_dt) or date.today()
        for item in payload:
            try:
                saver(
                    sector_name=str(item.get("sector") or ""),
                    trade_date=trade_date,
                    as_of=as_of_dt,
                    component_scores=_jsonable(item.get("components") or {}),
                    final_score=float(item.get("strength_score") or 0.0),
                    feature_version=str(item.get("feature_version") or DEFAULT_SECTOR_CALCULATION_VERSION),
                    sector_code=item.get("sector_code"),
                    universe_size=int(item.get("universe_size") or 0),
                    valid_symbol_count=int(item.get("valid_symbol_count") or 0),
                    coverage=float(item.get("coverage") or 0.0),
                    quality_flags=list(item.get("quality_flags") or []),
                )
            except Exception:  # noqa: BLE001 - 持久化失败不影响计算结果
                logger.warning("save_sector_snapshot failed", exc_info=True)


def _load_config_section(filename: str, section: str) -> dict[str, Any]:
    try:
        data = load_yaml_config(filename)
    except FileNotFoundError:
        return {}
    return dict(data.get(section) or {})


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _enrich_payload(payload: dict[str, Any], feature: dict[str, Any]) -> dict[str, Any]:
    """补充旧版消费方期望的 reason / change_pct 字段（向后兼容）。"""
    return_1d = feature.get("return_1d_median")
    return_5d = feature.get("return_5d_median")
    payload["change_pct"] = round(float(return_1d), 4) if return_1d is not None else None
    coverage = feature.get("coverage")
    parts = []
    if return_1d is not None:
        parts.append(f"1日收益中位数 {float(return_1d):.2f}%")
    if return_5d is not None:
        parts.append(f"5日收益中位数 {float(return_5d):.2f}%")
    parts.append(f"成分覆盖度 {float(coverage) * 100:.1f}%" if coverage is not None else "成分覆盖度未知")
    payload["reason"] = "，".join(parts)
    return payload


def _snapshot_row_to_payload(row: Any, rank: int) -> dict[str, Any]:
    """将 SectorFeatureSnapshot 持久化行还原为 get_sector_strength 的 payload 形状。"""
    as_of = getattr(row, "as_of", None)
    if isinstance(as_of, datetime):
        as_of = as_of.isoformat()
    return {
        "sector": getattr(row, "sector_name", None),
        "sector_code": getattr(row, "sector_code", None),
        "strength_score": getattr(row, "final_score", None),
        "rank": rank,
        "universe_size": int(getattr(row, "universe_size", None) or 0),
        "valid_symbol_count": int(getattr(row, "valid_symbol_count", None) or 0),
        "coverage": getattr(row, "coverage", None),
        "components": dict(getattr(row, "component_scores", None) or {}),
        "as_of": as_of,
        "feature_version": getattr(row, "feature_version", None),
        "quality_flags": list(getattr(row, "quality_flags", None) or []),
    }


def _as_datetime(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _as_date(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value

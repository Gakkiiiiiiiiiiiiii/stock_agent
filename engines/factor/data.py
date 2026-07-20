"""因子计算所需的数据面板加载。

从行情数据源拉取股票池日线，对齐成 (n_symbols, n_days) 的特征面板。
QMT 桥接不可用时返回 warning，不抛异常（与 engines/market 惯例一致）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import yaml

from engines.factor.vocab import FEATURES
from engines.market.data_provider import batched, get_market_data_provider, group_history_rows, to_qmt_symbol
from financial_agent.models import KlineRecord
from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

_MIN_KLINE_DAYS = 60
_UNIVERSE_CONFIG = "config/factor_universe.yaml"


def load_universe(path: str | Path | None = None) -> list[str]:
    """读取默认股票池配置，失败时返回空列表。"""
    cfg_path = Path(path) if path else project_root() / _UNIVERSE_CONFIG
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        symbols = [str(s).strip() for s in data.get("symbols") or [] if str(s).strip()]
        return symbols[:5000]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取股票池配置失败 %s: %s", cfg_path, exc)
        return []


def _fetch_history(symbols: list[str], days: int) -> tuple[dict[str, list[KlineRecord]], str | None]:
    """批量拉取日线，返回 {symbol: [KlineRecord]} 与 warning。"""
    provider = get_market_data_provider()
    start = (date.today() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")

    if hasattr(provider, "bridge"):
        try:
            qmt_symbols = [to_qmt_symbol(s) for s in symbols]
            rows: list[dict] = []
            # prefer_cache_first=False：桥接的缓存优先模式只在缓存完全缺失时下载，
            # 不会补齐到 end_time 的增量数据，会导致面板停在旧日期
            for chunk in batched(qmt_symbols, 200):
                rows.extend(provider.bridge.get_history(chunk, "1d", start, end, "front", prefer_cache_first=False))
            grouped = group_history_rows(rows)
            if grouped:
                return grouped, None
            return {}, "QMT 行情桥接未返回任何K线数据"
        except Exception as exc:  # noqa: BLE001
            logger.warning("QMT 批量取数失败，尝试逐标的兜底: %s", exc)

    # 兜底：逐标的 get_kline（本地样例数据等场景）
    grouped: dict[str, list[KlineRecord]] = {}
    warnings: list[str] = []
    for symbol in symbols:
        try:
            resp = provider.get_kline(symbol)
            if resp.records:
                grouped[symbol] = list(resp.records)
            if resp.warning:
                warnings.append(f"{symbol}: {resp.warning}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{symbol}: {exc}")
    return grouped, "; ".join(warnings) if warnings else None


def load_factor_panel(
    symbols: list[str] | None = None,
    days: int = 250,
) -> tuple[dict[str, np.ndarray], list[str], list[str], str | None]:
    """加载特征面板。

    返回 (features, dates, symbols, warning)：
    - features: dict[特征名, (n_symbols, n_days) ndarray]，缺值为 NaN；
    - dates: 交易日（YYYY-MM-DD）升序列表，取各标的日期并集；
    - symbols: 实际纳入的标的列表（剔除 K 线不足的标的）。
    """
    if not symbols:
        symbols = load_universe()
    if not symbols:
        return {}, [], [], "股票池为空（config/factor_universe.yaml 未配置或读取失败）"

    grouped, warning = _fetch_history(symbols, days)
    if not grouped:
        return {}, [], [], warning or "未获取到任何行情数据"

    # 剔除 K 线不足的标的
    valid = {s: recs for s, recs in grouped.items() if len(recs) >= _MIN_KLINE_DAYS}
    if not valid:
        return {}, [], [], (warning or "") + f"; 所有标的K线均不足 {_MIN_KLINE_DAYS} 根"

    # 日期并集（升序），保留最近 days 个交易日
    all_dates = sorted({r.date for recs in valid.values() for r in recs})
    all_dates = all_dates[-days:]
    date_index = {d: i for i, d in enumerate(all_dates)}
    n_days = len(all_dates)
    ordered_symbols = sorted(valid.keys())
    n_symbols = len(ordered_symbols)

    panels = {name: np.full((n_symbols, n_days), np.nan, dtype=float) for name in FEATURES}
    for si, symbol in enumerate(ordered_symbols):
        for rec in valid[symbol]:
            di = date_index.get(rec.date)
            if di is None:
                continue
            panels["open"][si, di] = rec.open
            panels["high"][si, di] = rec.high
            panels["low"][si, di] = rec.low
            panels["close"][si, di] = rec.close
            panels["volume"][si, di] = rec.volume
            panels["amount"][si, di] = rec.amount
            panels["turnover"][si, di] = rec.turnover_rate if rec.turnover_rate is not None else 0.0

    with np.errstate(invalid="ignore", divide="ignore"):
        panels["vwap"] = np.where(panels["volume"] > 0,
                                  panels["amount"] / np.where(panels["volume"] > 0, panels["volume"], 1.0),
                                  np.nan)
        prev_close = np.full_like(panels["close"], np.nan)
        prev_close[:, 1:] = panels["close"][:, :-1]
        panels["ret"] = np.where(prev_close > 0, panels["close"] / np.where(prev_close > 0, prev_close, 1.0) - 1.0, np.nan)

    dates = [d.isoformat() for d in all_dates]

    # 视频知识库特征（按视频发布时间对齐，无前视；数据缺失时全零面板）
    try:
        from engines.factor.video_features import build_video_feature_panel

        video_panels, video_warning = build_video_feature_panel(ordered_symbols, dates)
        panels.update(video_panels)
        if video_warning:
            logger.info("视频特征: %s", video_warning)
    except Exception as exc:  # noqa: BLE001
        logger.warning("视频特征构建失败（置零跳过）: %s", exc)
        panels["event_heat"][:] = 0.0
        panels["theme_sentiment"][:] = 0.0

    return panels, dates, ordered_symbols, warning


__all__ = ["load_factor_panel", "load_universe"]

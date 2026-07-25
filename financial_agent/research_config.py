from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from financial_agent.utils import project_root


class DataSplitConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_days: int = 500
    discovery_days: int = 250
    final_oos_days: int = 100
    max_warmup_days: int = 130
    discovery_ratio: float | None = None
    final_oos_withheld_from_prompt: bool = True


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    horizon_days: int = 5
    min_coverage: float = 0.6
    min_rank_ic: float = 0.02
    min_icir: float = 0.3
    min_topk_excess_annual_return: float = 0.0


class PurgedWalkForwardConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    n_windows: int = 3
    embargo_days: int | None = 5
    min_window_pass_ratio: float = 0.6
    min_positive_rank_ic_ratio: float = 0.6
    min_oos_excess_return: float = 0.0
    min_rank_ic_floor: float = -0.02


class PaperTradingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scoring_panel_days: int = 120
    mining_panel_days: int = 500
    remine_days: int = 5
    scoring_buffer_days: int = 10


class HighPositionConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_pool_size: int = 10
    min_valid_count: int = 10
    min_quote_coverage: float = 0.8
    near_high_ratio: float = 0.95
    ret20_quantile: float = 0.9
    ret60_quantile: float = 0.9
    amount_ratio_quantile: float = 0.8
    prev_close_mismatch_threshold: float = 0.01
    max_mismatch_ratio: float = 0.05


class NeutralizationConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "NOT_AVAILABLE"
    required_exposures: list[str] = ["industry", "log_market_cap", "beta", "liquidity"]


class FactorLibraryConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lock_timeout_seconds: int = 30


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data_split: DataSplitConfig = DataSplitConfig()
    evaluation: EvaluationConfig = EvaluationConfig()
    purged_walkforward: PurgedWalkForwardConfig = PurgedWalkForwardConfig()
    paper_trading: PaperTradingConfig = PaperTradingConfig()
    high_position: HighPositionConfig = HighPositionConfig()
    neutralization: NeutralizationConfig = NeutralizationConfig()
    factor_library: FactorLibraryConfig = FactorLibraryConfig()

    def validate_runtime(self) -> None:
        required = (
            self.data_split.max_warmup_days
            + self.data_split.discovery_days
            + self.data_split.final_oos_days
            + self.evaluation.horizon_days
        )
        if self.paper_trading.mining_panel_days < required:
            raise ValueError(
                "paper_trading.mining_panel_days must be >= "
                "data_split.max_warmup_days + data_split.discovery_days + "
                "data_split.final_oos_days + evaluation.horizon_days "
                f"({self.paper_trading.mining_panel_days} < {required})"
            )


@lru_cache(maxsize=1)
def get_research_config(path: str | Path | None = None) -> ResearchConfig:
    cfg_path = Path(path) if path else project_root() / "config" / "factor_research.yaml"
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raw = {}
    data = _apply_env_overrides(raw)
    config = ResearchConfig.model_validate(data)
    config.validate_runtime()
    return config


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(data)
    _set_nested(merged, "data_split.total_days", _env_int("FACTOR_RESEARCH_TOTAL_DAYS"))
    _set_nested(merged, "data_split.discovery_days", _env_int("FACTOR_RESEARCH_DISCOVERY_DAYS"))
    _set_nested(merged, "data_split.final_oos_days", _env_int("FACTOR_RESEARCH_FINAL_OOS_DAYS"))
    _set_nested(merged, "data_split.max_warmup_days", _env_int("FACTOR_RESEARCH_MAX_WARMUP_DAYS"))
    _set_nested(merged, "evaluation.horizon_days", _env_int("FACTOR_MINING_HORIZON_DAYS"))
    _set_nested(merged, "evaluation.min_coverage", _env_float("FACTOR_MIN_COVERAGE"))
    _set_nested(merged, "evaluation.min_rank_ic", _env_float("FACTOR_MIN_RANK_IC"))
    _set_nested(merged, "evaluation.min_icir", _env_float("FACTOR_MIN_ICIR"))
    _set_nested(merged, "evaluation.min_topk_excess_annual_return", _env_float("FACTOR_MIN_TOPK_EXCESS_ANNUAL_RETURN"))
    _set_nested(merged, "purged_walkforward.n_windows", _env_int("FACTOR_OOS_N_WINDOWS"))
    _set_nested(merged, "purged_walkforward.embargo_days", _env_int("FACTOR_OOS_EMBARGO_DAYS"))
    _set_nested(merged, "paper_trading.scoring_panel_days", _env_int("FACTOR_PAPER_SCORING_PANEL_DAYS"))
    _set_nested(merged, "paper_trading.mining_panel_days", _env_int("FACTOR_PAPER_MINING_PANEL_DAYS"))
    _set_nested(merged, "paper_trading.remine_days", _env_int("FACTOR_PAPER_REMINE_DAYS"))
    _set_nested(merged, "paper_trading.scoring_buffer_days", _env_int("FACTOR_PAPER_SCORING_BUFFER_DAYS"))
    _set_nested(merged, "factor_library.lock_timeout_seconds", _env_int("FACTOR_LIBRARY_LOCK_TIMEOUT_SECONDS"))
    return merged


def _set_nested(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    if value is None:
        return
    current = data
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return int(value)


def _env_float(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return float(value)


__all__ = [
    "ResearchConfig",
    "DataSplitConfig",
    "EvaluationConfig",
    "PurgedWalkForwardConfig",
    "PaperTradingConfig",
    "HighPositionConfig",
    "FactorLibraryConfig",
    "get_research_config",
]

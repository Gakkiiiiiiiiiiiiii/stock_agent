"""Agent-owned market/backtest configuration.

Factor research, mining and paper-trading configuration lives in stock_factor.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
    block_on_historical_risk_status_missing: bool = False


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fail_on_ambiguous_price_limit: bool = False
    fail_on_invalid_price_limit_meta: bool = True


class WalkForwardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gap_policy: Literal["cash", "hold_last_target"] = "cash"
    overlapping_target_policy: Literal["replace"] = "replace"
    retry_unfilled_target: bool = False


class ResearchConfig(BaseModel):
    """Compatibility name for the remaining Agent research controls."""

    model_config = ConfigDict(extra="ignore")
    high_position: HighPositionConfig = HighPositionConfig()
    backtest: BacktestConfig = BacktestConfig()
    walkforward: WalkForwardConfig = WalkForwardConfig()


@lru_cache(maxsize=1)
def get_research_config() -> ResearchConfig:
    return ResearchConfig(
        backtest=BacktestConfig(
            fail_on_ambiguous_price_limit=_env_bool("BACKTEST_FAIL_ON_AMBIGUOUS_PRICE_LIMIT", False),
            fail_on_invalid_price_limit_meta=_env_bool("BACKTEST_FAIL_ON_INVALID_PRICE_LIMIT_META", True),
        ),
        walkforward=WalkForwardConfig(
            gap_policy=os.getenv("STRATEGY_WALKFORWARD_GAP_POLICY", "cash"),
            overlapping_target_policy=os.getenv("STRATEGY_WALKFORWARD_OVERLAP_POLICY", "replace"),
            retry_unfilled_target=_env_bool("STRATEGY_WALKFORWARD_RETRY_UNFILLED_TARGET", False),
        ),
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value in (None, "") else value.strip().lower() in {"1", "true", "yes", "y", "on"}


__all__ = ["ResearchConfig", "HighPositionConfig", "BacktestConfig", "WalkForwardConfig", "get_research_config"]

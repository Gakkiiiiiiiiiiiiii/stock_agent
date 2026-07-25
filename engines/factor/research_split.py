from __future__ import annotations

from dataclasses import dataclass

from financial_agent.research_config import DataSplitConfig


@dataclass(frozen=True)
class FactorResearchSplit:
    warmup_start: int
    discovery_start: int
    discovery_end: int
    final_oos_start: int
    final_oos_end: int

    @property
    def discovery_days(self) -> int:
        return self.discovery_end - self.discovery_start

    @property
    def final_oos_days(self) -> int:
        return self.final_oos_end - self.final_oos_start

    @property
    def discovery_warmup_days(self) -> int:
        return self.discovery_start - self.warmup_start

    @property
    def final_oos_warmup_days(self) -> int:
        return self.final_oos_start - self.warmup_start

    def diagnostics(self, horizon: int, n_days: int) -> dict:
        return {
            "history_range": (self.warmup_start, self.discovery_start),
            "discovery_range": (self.discovery_start, self.discovery_end),
            "final_oos_range": (self.final_oos_start, self.final_oos_end),
            "future_return_observation_range": (self.final_oos_end, min(n_days, self.final_oos_end + horizon)),
            "latest_evaluable_day": max(0, n_days - horizon),
        }


def build_research_split(n_days: int, config: DataSplitConfig, horizon: int) -> FactorResearchSplit | None:
    latest_evaluable = n_days - horizon
    final_oos_end = latest_evaluable
    final_oos_start = final_oos_end - config.final_oos_days
    discovery_end = final_oos_start
    discovery_start = discovery_end - config.discovery_days
    warmup_start = max(0, discovery_start - config.max_warmup_days)
    if (
        horizon <= 0
        or final_oos_end <= 0
        or discovery_start <= warmup_start
        or discovery_end <= discovery_start
        or final_oos_start < discovery_end
        or final_oos_end <= final_oos_start
        or final_oos_end + horizon > n_days
    ):
        return None
    return FactorResearchSplit(
        warmup_start=warmup_start,
        discovery_start=discovery_start,
        discovery_end=discovery_end,
        final_oos_start=final_oos_start,
        final_oos_end=final_oos_end,
    )


__all__ = ["FactorResearchSplit", "build_research_split"]

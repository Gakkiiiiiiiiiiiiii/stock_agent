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


def build_research_split(n_days: int, config: DataSplitConfig, horizon: int) -> FactorResearchSplit | None:
    warmup_days = min(config.max_warmup_days, max(0, n_days - config.discovery_days - config.final_oos_days))
    discovery_start = warmup_days
    discovery_end = discovery_start + config.discovery_days
    final_oos_start = discovery_end
    final_oos_end = final_oos_start + config.final_oos_days
    if final_oos_end + horizon > n_days:
        overflow = final_oos_end + horizon - n_days
        warmup_days = max(0, warmup_days - overflow)
        discovery_start = warmup_days
        discovery_end = discovery_start + config.discovery_days
        final_oos_start = discovery_end
        final_oos_end = final_oos_start + config.final_oos_days
    if discovery_start <= 0 or discovery_end <= discovery_start or final_oos_end + horizon > n_days:
        return None
    return FactorResearchSplit(
        warmup_start=0,
        discovery_start=discovery_start,
        discovery_end=discovery_end,
        final_oos_start=final_oos_start,
        final_oos_end=final_oos_end,
    )


__all__ = ["FactorResearchSplit", "build_research_split"]

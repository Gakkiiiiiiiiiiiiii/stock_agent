from __future__ import annotations

from dataclasses import dataclass

from financial_agent.research_config import ResearchConfig, get_research_config


@dataclass(frozen=True)
class ResearchWindowRequirement:
    warmup_days: int
    discovery_days: int
    final_oos_days: int
    horizon_days: int
    minimum_required_days: int
    configured_total_days: int
    resolved_window_days: int


def resolve_research_window_requirement(
    config: ResearchConfig | None = None,
) -> ResearchWindowRequirement:
    cfg = config or get_research_config()
    minimum_required = (
        cfg.data_split.max_warmup_days
        + cfg.data_split.discovery_days
        + cfg.data_split.final_oos_days
        + cfg.evaluation.horizon_days
    )
    resolved = max(
        minimum_required,
        cfg.data_split.total_days,
        cfg.paper_trading.mining_panel_days,
    )
    return ResearchWindowRequirement(
        warmup_days=cfg.data_split.max_warmup_days,
        discovery_days=cfg.data_split.discovery_days,
        final_oos_days=cfg.data_split.final_oos_days,
        horizon_days=cfg.evaluation.horizon_days,
        minimum_required_days=minimum_required,
        configured_total_days=cfg.data_split.total_days,
        resolved_window_days=resolved,
    )


__all__ = ["ResearchWindowRequirement", "resolve_research_window_requirement"]

from financial_agent.research_config import DataSplitConfig
from engines.factor.research_split import build_research_split


def test_research_split_is_anchored_to_latest_evaluable_day():
    cfg = DataSplitConfig(discovery_days=30, final_oos_days=10, max_warmup_days=20)
    split = build_research_split(n_days=100, config=cfg, horizon=5)
    assert split is not None
    assert split.final_oos_end == 95
    assert split.final_oos_start == 85
    assert split.discovery_end == 85
    assert split.discovery_start == 55
    assert split.warmup_start == 35
    assert split.diagnostics(5, 100)["latest_evaluable_day"] == 95


def test_research_split_rejects_when_horizon_observation_is_unavailable():
    cfg = DataSplitConfig(discovery_days=30, final_oos_days=10, max_warmup_days=20)
    assert build_research_split(n_days=44, config=cfg, horizon=5) is None

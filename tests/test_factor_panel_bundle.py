"""FactorPanelBundle 与 Data Version 贯通测试（v2.2.3 第八轮 P1）。"""
import numpy as np
import pytest

from engines.factor.data import (
    FactorPanelBundle,
    build_panel_data_version,
    load_factor_panel_bundle,
)


def _synthetic_panel():
    panel = {
        "open": np.full((2, 5), 10.0),
        "close": np.full((2, 5), 10.0),
        "volume": np.full((2, 5), 1000.0),
    }
    dates = [f"2026-07-{d:02d}" for d in range(20, 25)]
    symbols = ["600000.SH", "600001.SH"]
    return panel, dates, symbols, None


def test_factor_panel_bundle_contains_data_metadata(monkeypatch):
    monkeypatch.setattr("engines.factor.data._build_panel", lambda symbols, days: _synthetic_panel())
    bundle = load_factor_panel_bundle(["600000.SH", "600001.SH"], days=5)
    assert isinstance(bundle, FactorPanelBundle)
    metadata = bundle.metadata
    assert metadata.data_version and metadata.data_version != "UNKNOWN"
    assert len(metadata.data_version) == 64
    assert metadata.data_snapshot_id.startswith("qmt-")
    assert metadata.source == "qmt"
    assert metadata.adjust == "front"
    assert metadata.period == "1d"
    assert metadata.start_date == "2026-07-20"
    assert metadata.end_date == "2026-07-24"
    assert len(metadata.universe_hash) == 64
    assert metadata.generated_at


def test_build_panel_data_version_reproducible():
    panel, dates, symbols, _ = _synthetic_panel()
    first = build_panel_data_version(symbols, dates, panel, "qmt", "front")
    second = build_panel_data_version(symbols, dates, panel, "qmt", "front")
    assert first == second  # 同一 Data Version 可重复复现
    changed = {key: value.copy() for key, value in panel.items()}
    changed["close"][0, 0] = 10.01
    assert build_panel_data_version(symbols, dates, changed, "qmt", "front") != first
    assert build_panel_data_version(symbols, dates, panel, "qmt", "none") != first


def test_load_factor_panel_legacy_tuple_still_works(monkeypatch):
    from engines.factor.data import load_factor_panel

    monkeypatch.setattr("engines.factor.data._build_panel", lambda symbols, days: _synthetic_panel())
    panel, dates, symbols, warning = load_factor_panel(["600000.SH"], days=5)
    assert panel and dates and symbols and warning is None


def test_paper_remine_passes_data_version_to_miner(tmp_path):
    from workers import factor_paper_worker as fpw

    captured: dict = {}

    class FakeMiner:
        def __init__(self, model_client=None):
            pass

        def mine(self, panel, symbols, **kwargs):
            captured.update(kwargs)
            return {"accepted": [], "warning": None, "diagnostics": {"run_valid": True, "oos_window_count": 1}}

    dates = [f"2026-07-{d:02d}" for d in range(20, 25)]
    result = fpw._maybe_remine(
        {"close": np.full((2, 5), 10.0)},
        ["600000.SH", "600001.SH"],
        dates,
        tmp_path,
        remine_days=0,
        miner_factory=FakeMiner,
        data_version="dv-123",
        data_snapshot_id="snap-456",
    )
    assert result["attempted"] is True
    assert captured["data_version"] == "dv-123"
    assert captured["data_snapshot_id"] == "snap-456"


def test_unpack_panel_supports_bundle_and_legacy_tuple():
    from engines.factor.data import FactorPanelMetadata
    from workers.factor_paper_worker import _unpack_panel

    panel, dates, symbols, warning = _synthetic_panel()
    bundle = FactorPanelBundle(
        panel=panel, dates=dates, symbols=symbols, warning=warning,
        metadata=FactorPanelMetadata(
            source="qmt", data_version="dv", data_snapshot_id="snap",
            generated_at="2026-07-26T00:00:00+00:00", start_date=dates[0], end_date=dates[-1],
            universe_hash="u", adjust="front", period="1d",
        ),
    )
    p, d, s, w, m = _unpack_panel(bundle)
    assert m.data_version == "dv"
    p2, d2, s2, w2, m2 = _unpack_panel((panel, dates, symbols, warning))
    assert m2.source == "legacy"
    assert m2.data_version == "UNKNOWN"


def test_require_data_version_blocks_oos_when_missing(tmp_path, monkeypatch):
    from financial_agent.research_config import ResearchConfig
    from engines.factor.miner import FactorMiner

    monkeypatch.setattr(
        "engines.factor.miner.get_research_config",
        lambda: ResearchConfig(require_data_version_for_oos=True),
    )

    class FakeClient:
        model = "fake"

        def available(self):
            return True

        def complete(self, prompt, **kwargs):
            return {"content": "[]"}

    miner = FactorMiner(model_client=FakeClient(), library_path=str(tmp_path / "lib.yaml"))
    panel = {"close": np.full((2, 400), 10.0), "ret": np.full((2, 400), 0.001)}
    result = miner.mine(panel, ["600000.SH", "600001.SH"], rounds=1, candidates_per_round=1)
    assert result["warning"] == "DATA_VERSION_REQUIRED"
    assert result["accepted"] == []
    assert result["diagnostics"]["run_failure_code"] == "DATA_VERSION_REQUIRED"

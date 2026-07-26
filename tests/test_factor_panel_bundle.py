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


# ---------- 生产版本贯通与哈希语义（v2.2.4 第九轮） ----------


def test_default_paper_worker_uses_panel_bundle():
    from engines.factor.data import load_factor_panel_bundle as bundle_loader
    from workers.factor_paper_worker import _default_panel_loader

    assert _default_panel_loader() is bundle_loader


def test_is_known_version_rejects_placeholders():
    from engines.factor.versioning import is_known_version

    assert is_known_version("dv-123") is True
    for value in (None, "", "  ", "UNKNOWN", "unknown", "NONE", "NULL", "N/A", "NA"):
        assert is_known_version(value) is False


@pytest.mark.parametrize("value", [None, "", "UNKNOWN", "NONE", "NULL", "N/A"])
def test_unknown_data_versions_are_rejected(tmp_path, monkeypatch, value):
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

    miner = FactorMiner(model_client=FakeClient(), library_path=str(tmp_path / "lib.yaml"))
    panel = {"close": np.full((2, 400), 10.0)}
    result = miner.mine(panel, ["600000.SH"], rounds=1, candidates_per_round=1,
                        data_version=value, data_snapshot_id="snap-1")
    assert result["warning"] == "DATA_VERSION_REQUIRED"


def test_unknown_snapshot_id_is_rejected(tmp_path, monkeypatch):
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

    miner = FactorMiner(model_client=FakeClient(), library_path=str(tmp_path / "lib.yaml"))
    panel = {"close": np.full((2, 400), 10.0)}
    result = miner.mine(panel, ["600000.SH"], rounds=1, candidates_per_round=1,
                        data_version="dv-1", data_snapshot_id="UNKNOWN")
    assert result["warning"] == "DATA_SNAPSHOT_ID_REQUIRED"


def test_default_remine_receives_real_data_version(tmp_path, monkeypatch):
    """生产默认链路：Bundle Loader 的真实版本经 _maybe_remine 传入 Miner。"""
    from workers import factor_paper_worker as fpw

    monkeypatch.setattr("engines.factor.data._build_panel", lambda symbols, days: _synthetic_panel())
    bundle = load_factor_panel_bundle(["600000.SH", "600001.SH"], days=5)

    captured: dict = {}

    class FakeMiner:
        def __init__(self, model_client=None):
            pass

        def mine(self, panel, symbols, **kwargs):
            captured.update(kwargs)
            return {"accepted": [], "warning": None, "diagnostics": {"run_valid": True, "oos_window_count": 1}}

    result = fpw._maybe_remine(
        bundle.panel, bundle.symbols, bundle.dates, tmp_path,
        remine_days=0, miner_factory=FakeMiner,
        data_version=bundle.metadata.data_version,
        data_snapshot_id=bundle.metadata.data_snapshot_id,
    )
    assert result["attempted"] is True
    assert captured["data_version"] != "UNKNOWN"
    assert captured["data_snapshot_id"] != "UNKNOWN"
    assert len(captured["data_version"]) == 64


def test_remine_strict_gate_blocks_legacy_unknown_version(tmp_path, monkeypatch):
    from financial_agent.research_config import ResearchConfig
    from workers import factor_paper_worker as fpw

    monkeypatch.setattr(
        fpw, "get_research_config",
        lambda: ResearchConfig(require_data_version_for_oos=True),
    )
    called = []

    class FakeMiner:
        def __init__(self, model_client=None):
            pass

        def mine(self, panel, symbols, **kwargs):
            called.append(1)
            return {"accepted": []}

    panel, dates, symbols, _ = _synthetic_panel()
    result = fpw._maybe_remine(
        panel, symbols, dates, tmp_path, remine_days=0,
        miner_factory=FakeMiner, data_version="UNKNOWN", data_snapshot_id="UNKNOWN",
    )
    assert result["failure_code"] == "DATA_VERSION_REQUIRED"
    assert not called  # 未进入 Miner


def test_nan_and_zero_produce_different_data_versions():
    _, dates, symbols, _ = _synthetic_panel()
    with_nan = {"close": np.array([[np.nan, 1.0], [2.0, 3.0]])}
    with_zero = {"close": np.array([[0.0, 1.0], [2.0, 3.0]])}
    assert build_panel_data_version(symbols, dates, with_nan, "qmt", "front") != \
        build_panel_data_version(symbols, dates, with_zero, "qmt", "front")


def test_positive_infinity_and_large_number_differ():
    _, dates, symbols, _ = _synthetic_panel()
    with_inf = {"close": np.array([[np.inf, 1.0], [2.0, 3.0]])}
    with_large = {"close": np.array([[1e308, 1.0], [2.0, 3.0]])}
    assert build_panel_data_version(symbols, dates, with_inf, "qmt", "front") != \
        build_panel_data_version(symbols, dates, with_large, "qmt", "front")


def test_symbol_order_changes_data_version():
    panel, dates, symbols, _ = _synthetic_panel()
    version = build_panel_data_version(symbols, dates, panel, "qmt", "front")
    reversed_symbols = list(reversed(symbols))
    reversed_panel = {key: value[::-1].copy() for key, value in panel.items()}
    assert build_panel_data_version(reversed_symbols, dates, reversed_panel, "qmt", "front") != version

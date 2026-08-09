"""§6.7 External Verification 验收测试。

覆盖：价格 MATCH、PE 误判防护（VALUATION 不再与 close 比对）、
净利润同比增长 MATCH、数据源不可用降级（ERROR/NOT_FOUND，禁止静默 MATCH）、
两轴独立（外部 MATCH 不改 support_status）、composite metric-aware 路由、
公告/政策类永不进入 EXTERNALLY_VERIFIED（factual_qa 因此不会用视频知识回答）。
"""

from __future__ import annotations

import pytest

from engines.content.external_fact_verifier import ExternalFactVerifier
from engines.content.external_verification.factory import CompositeProvider
from engines.content.external_verification.filing_provider import FilingVerificationProvider
from engines.content.external_verification.fundamental_provider import FundamentalVerificationProvider
from engines.content.external_verification.market_data_provider import MarketDataVerificationProvider
from engines.content.external_verification.policy_provider import PolicyVerificationProvider
from engines.retrieval.knowledge_access_policy import KnowledgeAccessPolicy


class _FakeMarketClient:
    def __init__(self, close: float | None, as_of: str = "2026-08-08") -> None:
        self._records = [] if close is None else [{"close": close, "date": as_of}]

    def get_kline(self, symbol: str):
        return {"records": self._records}


class _FakeBridge:
    def __init__(self, data: dict | None = None, error: Exception | None = None) -> None:
        self._data = data or {}
        self._error = error

    def get_financial_data(self, symbols, tables, start_date=None, end_date=None, report_type="announce_time"):
        if self._error is not None:
            raise self._error
        return self._data


def _unit(**overrides) -> dict:
    unit = {
        "knowledge_kind": "PRICE_LEVEL",
        "predicate_key": "price",
        "statement": "宁德时代当前价格30元。",
        "support_status": "SOURCE_SUPPORTED",
        "verification_status": "SOURCE_SUPPORTED",
        "truth_status": "NOT_CHECKED",
        "entities": [{"entity_type": "SECURITY", "ticker": "300750", "entity_name": "宁德时代"}],
    }
    unit.update(overrides)
    return unit


def _pe_unit() -> dict:
    return _unit(knowledge_kind="VALUATION", predicate_key="pe", statement="宁德时代当前 PE 约 20 倍")


def _pe_bridge_data() -> dict:
    # EPS = 300 / 20.4，使 close=300 时 PE 恰为 20.4
    return {
        "300750.SZ": {
            "PershareIndex": [
                {"m_timetag": "20251231", "m_anntime": "20260315", "s_fa_eps_basic": 300.0 / 20.4},
            ]
        }
    }


def _profit_bridge_data(with_prior: bool = True) -> dict:
    rows = [{"m_timetag": "20241231", "m_anntime": "20250301", "parentnetprofit": 1.201e10}]
    if with_prior:
        rows.append({"m_timetag": "20231231", "m_anntime": "20240301", "parentnetprofit": 1.0e10})
    return {"300750.SZ": {"Income": rows}}


def _composite(bridge_data: dict | None = None, close: float | None = 30.1) -> CompositeProvider:
    market_client = _FakeMarketClient(close)
    return CompositeProvider(
        [
            MarketDataVerificationProvider(market_client=market_client),
            FundamentalVerificationProvider(bridge_client=_FakeBridge(bridge_data), market_client=market_client),
            FilingVerificationProvider(),
            PolicyVerificationProvider(),
        ]
    )


# ---------- Price（§6.7） ----------

def test_price_claim_match():
    provider = MarketDataVerificationProvider(market_client=_FakeMarketClient(30.1))
    result = provider.verify(_unit(statement="宁德时代当前价格30元。"))
    assert result["status"] == "MATCH"
    assert result["observed_value"] == 30.1
    assert result["unit"] == "CNY"


# ---------- PE 误判防护（§5.2B / §6.7） ----------

def test_market_provider_no_longer_supports_valuation():
    provider = MarketDataVerificationProvider(market_client=_FakeMarketClient(300.0))
    assert provider.supports(_pe_unit()) is False
    # target_price / market_cap 也无法用 close 验证，不再路由到 market
    assert provider.supports(_unit(knowledge_kind="FACT", predicate_key="target_price")) is False
    assert provider.supports(_unit(knowledge_kind="FACT", predicate_key="market_cap")) is False


def test_pe_claim_compared_to_pe_not_close():
    provider = FundamentalVerificationProvider(
        bridge_client=_FakeBridge(_pe_bridge_data()),
        market_client=_FakeMarketClient(300.0),
    )
    unit = _pe_unit()
    assert provider.supports(unit) is True
    result = provider.verify(unit)
    assert result["status"] == "MATCH"  # 20 vs 20.4，绝不允许 20 vs 300 → CONFLICT
    assert result["observed_value"] == pytest.approx(20.4, abs=1e-3)
    assert result["unit"] == "MULTIPLE"
    assert result["source_type"] == "FUNDAMENTAL"


def test_pe_claim_conflict_when_pe_far_off():
    provider = FundamentalVerificationProvider(
        bridge_client=_FakeBridge(_pe_bridge_data()),
        market_client=_FakeMarketClient(600.0),  # PE = 40.8
    )
    result = provider.verify(_pe_unit())
    assert result["status"] == "CONFLICT"
    assert result["observed_value"] == pytest.approx(40.8, abs=1e-3)


# ---------- Financial Metric（§6.7） ----------

def test_profit_yoy_growth_match():
    provider = FundamentalVerificationProvider(bridge_client=_FakeBridge(_profit_bridge_data()))
    unit = _unit(knowledge_kind="FINANCIAL_METRIC", predicate_key="profit", statement="宁德时代净利润同比增长20%")
    assert provider.supports(unit) is True
    result = provider.verify(unit)
    assert result["status"] == "MATCH"
    assert result["observed_value"] == pytest.approx(20.1, abs=1e-6)
    assert result["unit"] == "PERCENT"


def test_profit_growth_not_found_without_prior_period():
    provider = FundamentalVerificationProvider(bridge_client=_FakeBridge(_profit_bridge_data(with_prior=False)))
    unit = _unit(knowledge_kind="FINANCIAL_METRIC", predicate_key="profit", statement="宁德时代净利润同比增长20%")
    result = provider.verify(unit)
    assert result["status"] == "NOT_FOUND"
    assert result["reason"] == "NO_PRIOR_PERIOD_DATA"


def test_fundamental_supports_false_when_metric_unrecognized():
    provider = FundamentalVerificationProvider(bridge_client=_FakeBridge(_profit_bridge_data()))
    unit = _unit(knowledge_kind="FINANCIAL_METRIC", predicate_key="industry_policy", statement="行业前景广阔")
    assert provider.supports(unit) is False


# ---------- Source unavailable：禁止静默 MATCH（§6.7） ----------

def test_fundamental_error_when_bridge_fails():
    provider = FundamentalVerificationProvider(bridge_client=_FakeBridge(error=RuntimeError("bridge down")))
    result = provider.verify(_pe_unit())
    assert result["status"] == "ERROR"
    assert result["reason"] == "FUNDAMENTAL_DATA_UNAVAILABLE"


def test_fundamental_not_found_when_no_data():
    provider = FundamentalVerificationProvider(bridge_client=_FakeBridge({}))
    result = provider.verify(_pe_unit())
    assert result["status"] == "NOT_FOUND"
    assert result["reason"] == "NO_FUNDAMENTAL_DATA"
    assert result["status"] != "MATCH"


# ---------- 两轴独立（§6.7） ----------

def test_external_match_does_not_change_support_status(monkeypatch):
    monkeypatch.setenv("VIDEO_EXTERNAL_FACT_VERIFICATION", "1")
    provider = FundamentalVerificationProvider(
        bridge_client=_FakeBridge(_pe_bridge_data()),
        market_client=_FakeMarketClient(300.0),
    )
    result = ExternalFactVerifier(provider=provider).verify_many([_pe_unit()])[0]
    assert result["truth_status"] == "EXTERNALLY_VERIFIED"
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert result["verification_status"] == "SOURCE_SUPPORTED"


# ---------- Composite metric-aware 路由（§6.5） ----------

def test_composite_routes_valuation_to_fundamental_not_market():
    composite = _composite(bridge_data=_pe_bridge_data(), close=300.0)
    result = composite.verify(_pe_unit())
    assert result["source_type"] == "FUNDAMENTAL"
    assert result["status"] == "MATCH"


def test_composite_routes_fact_price_predicate_to_market():
    composite = _composite(close=30.1)
    result = composite.verify(_unit(knowledge_kind="FACT", predicate_key="price", statement="宁德时代当前价格30元。"))
    assert result["source_type"] == "MARKET_DATA"
    assert result["status"] == "MATCH"


def test_composite_fact_unknown_predicate_not_found():
    composite = _composite()
    result = composite.verify(_unit(knowledge_kind="FACT", predicate_key="target_price", statement="目标价400元"))
    assert result["status"] == "NOT_FOUND"
    assert result["reason"] == "NO_PROVIDER_SUPPORTS"


def test_composite_routes_filing_predicate_to_filing_provider():
    composite = _composite()
    result = composite.verify(_unit(knowledge_kind="FACT", predicate_key="dividend", statement="公司宣布分红"))
    assert result["source_type"] == "OFFICIAL_FILING"
    assert result["status"] == "NOT_FOUND"
    assert result["reason"] == "EXTERNAL_VERIFICATION_NOT_SUPPORTED"


def test_composite_routes_policy_fact_to_policy_provider():
    composite = _composite()
    result = composite.verify({"knowledge_kind": "POLICY_FACT", "subject_key": "货币政策", "statement": "央行降准"})
    assert result["source_type"] == "POLICY_SOURCE"
    assert result["status"] == "NOT_FOUND"
    assert result["reason"] == "EXTERNAL_VERIFICATION_NOT_SUPPORTED"


# ---------- 公告/政策类永不 EXTERNALLY_VERIFIED（§6.3/§6.4 选项二） ----------

def test_policy_and_filing_never_reach_externally_verified(monkeypatch):
    monkeypatch.setenv("VIDEO_EXTERNAL_FACT_VERIFICATION", "1")
    verifier = ExternalFactVerifier(provider=_composite())
    policy_result = verifier.verify_many(
        [_unit(knowledge_kind="POLICY_FACT", subject_key="货币政策", predicate_key=None, statement="央行降准")]
    )[0]
    filing_result = verifier.verify_many(
        [_unit(knowledge_kind="FACT", predicate_key="dividend", statement="公司宣布分红")]
    )[0]
    # 外部核验不支持 → truth 只能是 NOT_FOUND，永远到不了 EXTERNALLY_VERIFIED
    assert policy_result["truth_status"] == "NOT_FOUND"
    assert filing_result["truth_status"] == "NOT_FOUND"
    # factual_qa 要求 EXTERNALLY_VERIFIED → 这两类 unit 被 RetrievalPolicy 排除
    allowed = KnowledgeAccessPolicy.for_intent("factual_qa").allowed_truth_status
    assert allowed == frozenset({"EXTERNALLY_VERIFIED"})
    assert policy_result["truth_status"] not in allowed
    assert filing_result["truth_status"] not in allowed

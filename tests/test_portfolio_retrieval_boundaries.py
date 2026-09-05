from app.tools.portfolio_tools import build_portfolio_tools


def test_portfolio_tools_depend_on_port_not_legacy_server():
    calls: list[tuple[str, dict]] = []

    class FakePortfolio:
        def rank_opportunities(self, candidates, context=None):
            payload = {"candidates": candidates, "context": context}
            calls.append(("rank", payload))
            return {"ranked": []}

        def construct_portfolio_v2(self, candidates, positions, context=None, risk_limits=None):
            payload = {"candidates": candidates, "positions": positions,
                       "context": context, "risk_limits": risk_limits}
            calls.append(("construct", payload))
            return {"actions": []}

    tools = build_portfolio_tools(FakePortfolio())
    assert tools[0].executor({"candidates": [], "context": None}) == {"ranked": []}
    assert tools[1].executor({"candidates": [], "positions": [], "context": None, "risk_limits": None}) == {"actions": []}
    assert [name for name, _ in calls] == ["rank", "construct"]


def test_portfolio_application_service_purely_delegates_to_fake_port():
    from app.application.portfolio import PortfolioApplicationService

    class FakePortfolio:
        def rank_opportunities(self, candidates, context=None):
            return {"operation": "rank", "count": len(candidates), "context": context}

        def construct_portfolio_v2(self, candidates, positions, context=None, risk_limits=None):
            return {"operation": "construct", "candidates": candidates, "positions": positions,
                    "context": context, "risk_limits": risk_limits}

    service = PortfolioApplicationService(FakePortfolio())
    assert service.rank_opportunities([{"symbol": "AAA"}], {"as_of": "2026-01-01"})["operation"] == "rank"
    assert service.construct_portfolio_v2([], [], risk_limits={"max_total_position": 0.5})["operation"] == "construct"


def test_local_retrieval_adapter_preserves_task_context(monkeypatch):
    from app.adapters.local import retrieval as module

    monkeypatch.setattr(module, "retrieve_memory", lambda **kwargs: {"items": [], "query": kwargs["query"]})
    result = module.LocalRetrievalAdapter().retrieve_relevant_context("gold", task_type="research", top_k=3)
    assert result == {"items": [], "query": "gold", "task_type": "research"}

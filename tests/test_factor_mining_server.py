from contracts.factor import AlphaScoreRequest, MiningJobRequest
from mcp_servers import factor_mining_server


class FakeFactorClient:
    def create_mining_job(self, request: MiningJobRequest):
        return {"job_id": "job-1", "symbols": request.symbols}

    def list_factors(self, *, limit: int):
        return {"items": [{"factor_id": "F001"}], "limit": limit}

    def evaluate(self, payload):
        return {"factor_id": payload["factor_id"], "symbols": payload["symbols"]}

    def score_alpha(self, request: AlphaScoreRequest):
        return {"items": [{"symbol": symbol, "alpha_score": 1.0}] for symbol in request.symbols}


def test_factor_mcp_tools_proxy_remote_service(monkeypatch):
    monkeypatch.setattr(factor_mining_server, "get_factor_client", lambda: FakeFactorClient())
    assert factor_mining_server.mine_factors(universe=["600000.SH"])["job_id"] == "job-1"
    assert factor_mining_server.list_factor_library(3)["limit"] == 3
    assert factor_mining_server.evaluate_factor("F001", universe=["600000.SH"])["factor_id"] == "F001"
    assert factor_mining_server.scan_alpha_factors(["600000.SH"])["items"][0]["symbol"] == "600000.SH"

import os
from fastapi import FastAPI, Response
from contracts.market import MarketFeatureRequest, SectorStrengthRequest
from engines.market.feature_service import MarketFeatureService, SectorFeatureService
from engines.market.qmt_bridge_client import QmtBridgeClient
from services.readiness import postgres_check
app = FastAPI(title="market-data-service")
@app.get("/health/live")
def live(): return {"status": "ok"}
@app.get("/health/ready")
def ready(response: Response):
    checks = {"postgres": postgres_check()}
    if os.getenv("MARKET_DATA_REQUIRE_QMT", "false").lower() in {"1", "true", "yes"}:
        try:
            QmtBridgeClient().healthcheck(); checks["qmt"] = "ok"
        except Exception:
            checks["qmt"] = "failed"
    else:
        checks["qmt"] = "optional"
    is_ready = checks["postgres"] == "ok" and checks["qmt"] != "failed"
    if not is_ready: response.status_code = 503
    return {"status": "ok" if is_ready else "degraded", "checks": checks}
@app.post("/v1/features")
def features(request: MarketFeatureRequest): return {"meta": request.model_dump(mode="json"), "result": MarketFeatureService().get_market_features(request.as_of)}
@app.post("/v1/sectors")
def sectors(request: SectorStrengthRequest): return {"meta": request.model_dump(mode="json"), "result": SectorFeatureService().get_sector_strength(request.top_k, request.as_of)}

from fastapi import FastAPI
from contracts.market import MarketFeatureRequest, SectorStrengthRequest
from engines.market.feature_service import MarketFeatureService, SectorFeatureService
app = FastAPI(title="market-data-service")
@app.get("/health/live")
def live(): return {"status": "ok"}
@app.get("/health/ready")
def ready(): return {"status": "ok"}
@app.post("/v1/features")
def features(request: MarketFeatureRequest): return {"meta": request.model_dump(mode="json"), "result": MarketFeatureService().get_market_features(request.as_of)}
@app.post("/v1/sectors")
def sectors(request: SectorStrengthRequest): return {"meta": request.model_dump(mode="json"), "result": SectorFeatureService().get_sector_strength(request.top_k, request.as_of)}

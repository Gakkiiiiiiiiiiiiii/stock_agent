import hashlib
import json
import os
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response

from contracts.market import BarsBatchRequest, MarketFeatureRequest, SectorStrengthRequest
from engines.market.data_provider import get_market_data_provider
from engines.market.feature_service import MarketFeatureService, SectorFeatureService
from engines.market.qmt_bridge_client import QmtBridgeClient
from services.readiness import postgres_check
app = FastAPI(title="market-data-service")


def _batch_payload(request: BarsBatchRequest) -> dict:
    if request.end < request.start:
        raise HTTPException(status_code=422, detail="end must be on or after start")

    provider = get_market_data_provider()
    by_symbol = {}
    sources = []
    all_dates = set()
    for symbol in request.symbols:
        response = provider.get_kline(symbol, request.start, request.end, "1d", request.adjust)
        sources.append(response.source)
        records = {record.date.isoformat(): record for record in response.records}
        by_symbol[symbol] = records
        all_dates.update(records)

    dates = sorted(all_dates)
    field_names = ("open", "high", "low", "close", "volume", "amount", "turnover")
    bars = {field: [] for field in field_names}
    for symbol in request.symbols:
        records = by_symbol[symbol]
        for field in field_names:
            attribute = "turnover_rate" if field == "turnover" else field
            bars[field].append([
                getattr(records[day], attribute) if day in records else None
                for day in dates
            ])

    source = sources[0] if len(set(sources)) == 1 else "mixed"
    version_material = {
        "symbols": request.symbols,
        "dates": dates,
        "bars": bars,
        "adjust": request.adjust,
        "source": source,
    }
    data_version = hashlib.sha256(
        json.dumps(version_material, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "symbols": request.symbols,
        "dates": dates,
        "bars": bars,
        "data_version": data_version,
        "data_snapshot_id": str(uuid4()),
        "source": source,
    }
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


@app.post("/v1/bars/batch")
def bars_batch(request: BarsBatchRequest):
    return {
        "contract_version": "market-data.v1",
        "meta": request.model_dump(mode="json"),
        "data": _batch_payload(request),
    }

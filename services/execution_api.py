"""Independently deployable execution boundary sharing the core service logic."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from sqlalchemy import text

from contracts.execution import TradeIntentRequest
from engines.execution.models import ExecutionMode
from engines.execution.qmt_live_adapter import QmtLiveAdapter
from engines.execution.service import ExecutionService, load_execution_config
from storage.bootstrap import create_all
from storage.db import session_scope
from storage.repositories.p2_repository import P2Repository

_service: ExecutionService | None = None


def service() -> ExecutionService:
    if _service is None:
        raise RuntimeError("EXECUTION_SERVICE_NOT_STARTED")
    return _service


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _service
    create_all()
    config = load_execution_config()
    mode = ExecutionMode(config.get("mode", "PAPER"))
    adapter = QmtLiveAdapter() if mode == ExecutionMode.LIVE else None
    _service = ExecutionService(mode=mode, adapter=adapter, repository=P2Repository())
    yield
    _service = None


app = FastAPI(title="execution-service", lifespan=lifespan)


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready(response: Response):
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        current = service().status()
        checks = {"postgres": "ok", "mode": current["mode"], "halted": current["halted"]}
        if current["mode"] == ExecutionMode.LIVE.value:
            adapter = service().adapter
            try:
                if adapter is None:
                    raise RuntimeError("LIVE_ADAPTER_UNAVAILABLE")
                adapter.healthcheck()
                checks["qmt_execution"] = "ok"
            except Exception:
                checks["qmt_execution"] = "failed"
        if checks.get("qmt_execution") == "failed":
            response.status_code = 503
            return {"status": "degraded", "checks": checks}
        return {"status": "ok", "checks": checks}
    except Exception:
        response.status_code = 503
        return {"status": "degraded", "checks": {"postgres": "failed"}}


@app.get("/v1/status")
def status():
    return service().status()


@app.post("/v1/orders")
def order(request: TradeIntentRequest):
    result = service().create_order(request.intent, request.context, request.quantity)
    return {"meta": request.model_dump(mode="json", exclude={"intent", "context", "quantity"}), "result": result.model_dump(mode="json")}


@app.get("/v1/orders/{client_order_id}")
def get_order(client_order_id: str):
    result = service().order(client_order_id)
    if result is None:
        raise HTTPException(status_code=404, detail="ORDER_NOT_FOUND")
    return result.model_dump(mode="json")


@app.post("/v1/orders/{client_order_id}/submit")
def submit(client_order_id: str):
    try:
        return service().submit(client_order_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ORDER_NOT_FOUND") from exc


@app.post("/v1/orders/{client_order_id}/cancel")
def cancel(client_order_id: str):
    try:
        return service().cancel(client_order_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ORDER_NOT_FOUND") from exc


@app.post("/v1/paper/quotes/{symbol}")
def paper_quote(symbol: str, quote: dict):
    return {"orders": [item.model_dump(mode="json") for item in service().process_quote(symbol, quote)]}


@app.post("/v1/reconcile")
def reconcile_positions(local: dict, broker: dict):
    return service().reconcile(local, broker)

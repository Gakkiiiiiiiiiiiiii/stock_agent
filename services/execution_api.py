from contextlib import asynccontextmanager
from fastapi import FastAPI, Response
from contracts.execution import TradeIntentRequest
from engines.execution.service import ExecutionService
from storage.bootstrap import create_all
from storage.db import session_scope
from sqlalchemy import text
from storage.repositories.p2_repository import P2Repository

@asynccontextmanager
async def lifespan(_: FastAPI):
    create_all()
    yield

app = FastAPI(title="execution-service", lifespan=lifespan)
_service = ExecutionService(repository=P2Repository())
@app.get("/health/live")
def live(): return {"status": "ok"}
@app.get("/health/ready")
def ready(response: Response):
    try:
        with session_scope() as session: session.execute(text("SELECT 1"))
        return {"status": "ok", "checks": {"postgres": "ok"}}
    except Exception:
        response.status_code = 503
        return {"status": "degraded", "checks": {"postgres": "failed"}}
@app.post("/v1/orders")
def order(request: TradeIntentRequest): return {"meta": request.model_dump(mode="json", exclude={"intent", "context", "quantity"}), "result": _service.create_order(request.intent, request.context, request.quantity).model_dump(mode="json")}

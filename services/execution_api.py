from fastapi import FastAPI
from contracts.execution import TradeIntentRequest
from engines.execution.service import ExecutionService
app = FastAPI(title="execution-service")
_service = ExecutionService()
@app.get("/health/live")
def live(): return {"status": "ok"}
@app.get("/health/ready")
def ready(): return {"status": "ok"}
@app.post("/v1/orders")
def order(request: TradeIntentRequest): return {"meta": request.model_dump(mode="json", exclude={"intent", "context", "quantity"}), "result": _service.create_order(request.intent, request.context, request.quantity).model_dump(mode="json")}

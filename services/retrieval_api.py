from fastapi import FastAPI, Response

from app.adapters.local.retrieval import LocalRetrievalAdapter
from app.ports.retrieval import RetrievalPort
from contracts.retrieval import RetrievalRequest
from services.readiness import retrieval_checks

retrieval: RetrievalPort = LocalRetrievalAdapter()
app = FastAPI(title="retrieval-service")
@app.get("/health/live")
def live(): return {"status": "ok"}
@app.get("/health/ready")
def ready(response: Response):
    checks = retrieval_checks()
    is_ready = all(value == "ok" for value in checks.values())
    if not is_ready: response.status_code = 503
    return {"status": "ok" if is_ready else "degraded", "checks": checks}
@app.post("/v1/context")
def context(request: RetrievalRequest): return {"meta": request.model_dump(mode="json"), "result": retrieval.retrieve_relevant_context(query=request.query, task_type=request.task_type, filters=request.filters, top_k=request.top_k)}

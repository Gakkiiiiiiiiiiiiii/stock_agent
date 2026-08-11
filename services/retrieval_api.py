from fastapi import FastAPI
from contracts.retrieval import RetrievalRequest
from mcp_servers.retrieval_server import retrieve_relevant_context
app = FastAPI(title="retrieval-service")
@app.get("/health/live")
def live(): return {"status": "ok"}
@app.get("/health/ready")
def ready(): return {"status": "ok"}
@app.post("/v1/context")
def context(request: RetrievalRequest): return {"meta": request.model_dump(mode="json"), "result": retrieve_relevant_context(query=request.query, task_type=request.task_type, filters=request.filters, top_k=request.top_k)}

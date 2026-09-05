from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import dependencies
from app.application.audit.lineage_graph import LineageGraph

router = APIRouter(prefix="/api/v2/audit", tags=["audit"])


def _graph() -> LineageGraph:
    graph = getattr(dependencies, "lineage_graph", None)
    if graph is None:
        graph = LineageGraph()
        dependencies.lineage_graph = graph
    return graph


@router.get("/decisions/{decision_id}/lineage")
def get_lineage(decision_id: str) -> dict:
    value = _graph().get(decision_id)
    if value is None:
        raise HTTPException(status_code=404, detail="decision lineage not found")
    return value

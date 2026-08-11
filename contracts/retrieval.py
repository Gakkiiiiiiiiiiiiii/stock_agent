from pydantic import Field
from contracts.common import ServiceEnvelope
class RetrievalRequest(ServiceEnvelope):
    query: str
    task_type: str | None = None
    filters: dict = Field(default_factory=dict)
    top_k: int = 10

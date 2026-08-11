from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
from pydantic import BaseModel, Field

class ServiceEnvelope(BaseModel):
    schema_version: int = 1
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))

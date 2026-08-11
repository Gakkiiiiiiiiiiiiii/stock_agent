from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DomainResultMeta(BaseModel):
    as_of: datetime | None = None
    data_source: str | None = None
    data_version: str | None = None
    calculation_version: str | None = None
    confidence: float | None = None
    coverage: float | None = None
    quality_flags: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

from datetime import date
from typing import Literal

from pydantic import Field

from contracts.common import ServiceEnvelope


class MarketFeatureRequest(ServiceEnvelope):
    pass


class SectorStrengthRequest(ServiceEnvelope):
    top_k: int = 20


class BarsBatchRequest(ServiceEnvelope):
    """Stable HTTP contract used by the remote Factor service."""

    symbols: list[str] = Field(min_length=1, max_length=500)
    start: date
    end: date
    adjust: Literal["qfq", "hfq", "none"] = "qfq"

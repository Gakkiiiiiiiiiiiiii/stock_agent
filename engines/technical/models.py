from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TriState(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    alias: str
    params: dict[str, Any]


@dataclass(frozen=True)
class TechnicalProfile:
    name: str
    version: str
    frequency: str
    minimum_bars: int
    price_adjustment: str
    indicators: list[IndicatorSpec]
    output_precision: int = 8


@dataclass
class RuleEvaluation:
    rule_id: str
    rule_version: str
    status: TriState
    score_awarded: float
    max_score: float
    evidence: list[str] = field(default_factory=list)
    node_results: list[dict[str, Any]] = field(default_factory=list)

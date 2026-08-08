from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from engines.memory.models import MemoryCandidate, MemoryExtractionInput


class GroundingResult(BaseModel):
    grounded: bool
    unsupported_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence_penalty: float = 0.0


class MemoryGroundingValidator:
    """Deterministically reject high-risk LLM facts absent from source evidence."""

    _stock = re.compile(r"(?<![A-Za-z0-9])\d{6}\.(?:SH|SZ)(?![A-Za-z0-9])", re.IGNORECASE)
    _date = re.compile(r"(?<![0-9])\d{4}-\d{2}-\d{2}(?![0-9])")
    _number = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?%?")

    def validate(self, candidate: MemoryCandidate, value: MemoryExtractionInput) -> GroundingResult:
        source = f"{value.text}\n{json.dumps(value.metadata, ensure_ascii=False, default=str)}"
        generated = f"{candidate.summary}\n{json.dumps(candidate.facts, ensure_ascii=False, default=str)}\n{' '.join(candidate.lessons)}"
        unsupported: list[str] = []
        for label, pattern in (("stock_codes", self._stock), ("dates", self._date), ("numbers", self._number)):
            source_values = {item.lower() for item in pattern.findall(source)}
            generated_values = {item.lower() for item in pattern.findall(generated)}
            if generated_values - source_values:
                unsupported.append(label)
        regime = candidate.facts.get("market_regime") if isinstance(candidate.facts, dict) else None
        known_regime = value.metadata.get("market_regime") or (value.metadata.get("facts") or {}).get("market_regime")
        if regime and known_regime and regime != known_regime:
            unsupported.append("market_regime")
        return GroundingResult(
            grounded=not unsupported,
            unsupported_fields=unsupported,
            warnings=["LLM_MEMORY_UNSUPPORTED_FACTS"] if unsupported else [],
            confidence_penalty=0.25 if unsupported else 0.0,
        )

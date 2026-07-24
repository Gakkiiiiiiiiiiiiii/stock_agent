from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from engines.technical.models import TechnicalProfile

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class IndicatorDefinition:
    name: str
    version: str
    required_fields: tuple[str, ...]
    warmup_bars: int
    calculator: Callable[[pd.DataFrame, dict[str, Any]], pd.Series | pd.DataFrame]

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return dict(params or {})

    def calculate(self, frame: pd.DataFrame, params: dict[str, Any]) -> pd.Series | pd.DataFrame:
        return self.calculator(frame, self.validate_params(params))


class IndicatorRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], IndicatorDefinition] = {}

    def register(self, definition: IndicatorDefinition) -> None:
        key = (definition.name, definition.version)
        if key in self._definitions:
            raise ValueError(f"duplicate indicator definition: {definition.name}@{definition.version}")
        if not SEMVER_RE.match(definition.version):
            raise ValueError(f"indicator version must be semver: {definition.version}")
        self._definitions[key] = definition

    def get(self, name: str, version: str | None = None) -> IndicatorDefinition:
        matches = [item for (item_name, item_version), item in self._definitions.items() if item_name == name and (version is None or item_version == version)]
        if not matches:
            raise KeyError(f"indicator not registered: {name}@{version or 'latest'}")
        return sorted(matches, key=lambda item: item.version)[-1]

    def validate_profile(self, profile: TechnicalProfile, fields: set[str] | None = None) -> dict[str, Any]:
        aliases = set()
        errors = []
        available = fields or {"open", "high", "low", "close", "volume", "amount", "return"}
        for spec in profile.indicators:
            if spec.alias in aliases:
                errors.append(f"duplicate alias: {spec.alias}")
            aliases.add(spec.alias)
            try:
                definition = self.get(spec.name)
            except KeyError as exc:
                errors.append(str(exc))
                continue
            missing = [field for field in definition.required_fields if field not in available]
            if missing:
                errors.append(f"{spec.alias} missing fields: {missing}")
        return {"valid": not errors, "errors": errors}

    def fingerprint(self, profile: TechnicalProfile) -> str:
        payload = {
            "name": profile.name,
            "version": profile.version,
            "frequency": profile.frequency,
            "minimum_bars": profile.minimum_bars,
            "price_adjustment": profile.price_adjustment,
            "indicators": [spec.__dict__ for spec in profile.indicators],
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def default_indicator_registry() -> IndicatorRegistry:
    registry = IndicatorRegistry()
    registry.register(IndicatorDefinition("sma", "1.0.0", ("close",), 1, lambda frame, p: frame[p.get("field", "close")].rolling(int(p["window"])).mean()))
    registry.register(IndicatorDefinition("ema", "1.0.0", ("close",), 1, lambda frame, p: frame[p.get("field", "close")].ewm(span=int(p["window"]), adjust=False).mean()))
    registry.register(IndicatorDefinition("rolling_std", "1.0.0", ("return",), 1, lambda frame, p: frame[p.get("field", "return")].rolling(int(p["window"])).std()))
    registry.register(IndicatorDefinition("volume_ma", "1.0.0", ("volume",), 1, lambda frame, p: frame["volume"].rolling(int(p["window"])).mean()))
    registry.register(IndicatorDefinition("volume_ratio", "1.0.0", ("volume",), 1, lambda frame, p: frame["volume"] / frame["volume"].rolling(int(p["window"])).mean()))

    def macd(frame: pd.DataFrame, p: dict[str, Any]) -> pd.DataFrame:
        close = frame[p.get("field", "close")]
        fast = close.ewm(span=int(p.get("fast", 12)), adjust=False).mean()
        slow = close.ewm(span=int(p.get("slow", 26)), adjust=False).mean()
        dif = fast - slow
        dea = dif.ewm(span=int(p.get("signal", 9)), adjust=False).mean()
        return pd.DataFrame({"dif": dif, "dea": dea, "hist": (dif - dea) * 2}, index=frame.index)

    registry.register(IndicatorDefinition("macd", "1.0.0", ("close",), 26, macd))
    return registry

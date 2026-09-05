from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelRoute:
    name: str
    endpoint: Callable[[dict[str, Any]], dict[str, Any]]
    provider: str | None = None
    model: str | None = None
    model_version: str | None = None
    cost_per_input_token: float = 0.0
    cost_per_output_token: float = 0.0


class ModelRouter:
    def __init__(self, primary: ModelRoute, fallback: ModelRoute | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    def routes(self) -> tuple[ModelRoute, ...]:
        return (self.primary, self.fallback) if self.fallback is not None else (self.primary,)

"""Single state boundary shared by stream worker, API and opportunity refresh."""
from __future__ import annotations

import json


class RealtimeFeatureStateRepository:
    def __init__(self, client=None, prefix: str = "market:feature:state:v1") -> None:
        self.client, self.prefix = client, prefix
        self._memory: dict[str, dict] = {}

    def save_symbol(self, symbol: str, state: dict) -> None:
        key = f"{self.prefix}:{symbol}"
        if self.client is None: self._memory[key] = dict(state)
        else: self.client.set(key, json.dumps(state, ensure_ascii=False))

    def get_symbol(self, symbol: str) -> dict | None:
        key = f"{self.prefix}:{symbol}"
        value = self._memory.get(key) if self.client is None else self.client.get(key)
        if value is None: return None
        return dict(value) if isinstance(value, dict) else json.loads(value)

    def save_aggregate(self, state: dict) -> None:
        self.save_symbol("aggregate", state)

    def get_aggregate(self) -> dict | None:
        return self.get_symbol("aggregate")

    def checkpoint(self, engine) -> None:
        for symbol in engine._events: self.save_symbol(symbol, engine.symbol_features(symbol))
        self.save_aggregate(engine.aggregate_features())

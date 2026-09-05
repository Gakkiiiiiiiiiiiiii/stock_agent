from __future__ import annotations

from typing import Protocol


class Outbox(Protocol):
    def enqueue(self, *, event_id: str, aggregate_id: str, event_type: str, payload: dict) -> None: ...


class InMemoryOutbox:
    def __init__(self) -> None:
        self.events: dict[str, dict] = {}

    def enqueue(self, *, event_id: str, aggregate_id: str, event_type: str, payload: dict) -> None:
        self.events.setdefault(event_id, {"event_id": event_id, "aggregate_id": aggregate_id, "event_type": event_type, "payload": payload, "published": False})

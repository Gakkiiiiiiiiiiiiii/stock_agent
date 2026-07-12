from __future__ import annotations


class EventConflictResolver:
    def resolve(self, events: list[dict]) -> list[dict]:
        sorted_events = sorted(
            [dict(event) for event in events],
            key=lambda item: (int(item.get("start_ms") or 0), int(item.get("event_index") or 0)),
        )
        latest_by_key: dict[str, dict] = {}
        resolved: list[dict] = []
        for event in sorted_events:
            conflict_key = self._build_conflict_key(event)
            event["conflict_key"] = conflict_key
            event["conflict_status"] = event.get("conflict_status") or "active"
            if not conflict_key:
                resolved.append(event)
                continue
            previous = latest_by_key.get(conflict_key)
            if previous is None:
                latest_by_key[conflict_key] = event
                resolved.append(event)
                continue
            if self._is_superseding(previous, event):
                previous["conflict_status"] = "superseded"
                previous["superseded_by_event_id"] = event.get("event_index")
                event["conflict_status"] = "active"
                latest_by_key[conflict_key] = event
            elif self._same_direction(previous, event):
                event["conflict_status"] = "reinforced"
                latest_by_key[conflict_key] = event
            else:
                event["conflict_status"] = "conflicting"
                latest_by_key[conflict_key] = event
            resolved.append(event)
        return resolved

    def build_timeline(self, events: list[dict]) -> list[dict]:
        timeline = []
        for event in sorted(events, key=lambda item: (int(item.get("start_ms") or 0), int(item.get("event_index") or 0))):
            timeline.append(
                {
                    "start_ms": event.get("start_ms"),
                    "end_ms": event.get("end_ms"),
                    "event_type": event.get("event_type"),
                    "claim_type": event.get("claim_type"),
                    "sentiment": event.get("sentiment"),
                    "statement": event.get("statement"),
                    "conflict_status": event.get("conflict_status"),
                    "entities": event.get("entities") or [],
                    "condition_text": event.get("condition_text"),
                    "invalidation_text": event.get("invalidation_text"),
                }
            )
        return timeline

    @staticmethod
    def _build_conflict_key(event: dict) -> str | None:
        entities = event.get("entities") or []
        primary_entity = None
        if entities and isinstance(entities[0], dict):
            primary_entity = entities[0].get("ticker") or entities[0].get("name")
        event_type = str(event.get("event_type") or "").strip()
        if primary_entity and event_type:
            return f"{primary_entity}::{event_type}"
        if event_type:
            return f"topic::{event_type}"
        return None

    @staticmethod
    def _same_direction(previous: dict, current: dict) -> bool:
        return str(previous.get("sentiment") or "") == str(current.get("sentiment") or "")

    @staticmethod
    def _is_superseding(previous: dict, current: dict) -> bool:
        previous_sentiment = str(previous.get("sentiment") or "")
        current_sentiment = str(current.get("sentiment") or "")
        if previous_sentiment and current_sentiment and previous_sentiment != current_sentiment:
            return True
        if current.get("invalidation_text"):
            return True
        current_statement = str(current.get("statement") or "")
        return any(token in current_statement for token in ("失效", "不能破", "证伪", "不成立"))

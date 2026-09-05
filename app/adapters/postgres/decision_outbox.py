from __future__ import annotations

import json

from sqlalchemy import text

from storage.db import session_scope


class PostgresDecisionOutbox:
    def enqueue(self, *, event_id: str, aggregate_id: str, event_type: str, payload: dict) -> None:
        with session_scope() as session:
            session.execute(text("""INSERT INTO outbox(event_id, aggregate_id, event_type, payload)
                VALUES (:event_id, :aggregate_id, :event_type, :payload)
                ON CONFLICT (event_id) DO NOTHING"""), {"event_id": event_id, "aggregate_id": aggregate_id,
                "event_type": event_type, "payload": json.dumps(payload, sort_keys=True, ensure_ascii=False)})

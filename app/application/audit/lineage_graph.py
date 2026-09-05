from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from app.domain.lineage import DecisionLineageV1


class LineageGraph:
    """Build a safe, identifier-only audit DAG (never stores prompt text)."""
    def __init__(
        self,
        *,
        persistent: bool = False,
        session_factory: Callable[[], AbstractContextManager[Any]] | None = None,
    ) -> None:
        self._items: dict[str, DecisionLineageV1] = {}
        self.persistent = persistent
        self._session_factory = session_factory

    def record(self, lineage: DecisionLineageV1, session: Any | None = None) -> None:
        if self.persistent and session is not None:
            from sqlalchemy import text

            session.execute(text("""INSERT INTO decision_lineage(decision_id,payload_json)
                VALUES (:decision_id,:payload) ON CONFLICT(decision_id) DO UPDATE SET payload_json=:payload"""),
                {"decision_id": lineage.decision_id, "payload": json.dumps(lineage.model_dump(mode="json"), sort_keys=True)})
            # With a caller-owned transaction, leave the cache empty until a
            # later read observes the committed row.  This keeps rollback
            # invisible to same-process audit callers.
        elif self.persistent:
            from sqlalchemy import text

            if self._session_factory is None:
                raise RuntimeError("LINEAGE_SESSION_FACTORY_REQUIRED")

            with self._session_factory() as db_session:
                db_session.execute(text("""INSERT INTO decision_lineage(decision_id,payload_json)
                    VALUES (:decision_id,:payload) ON CONFLICT(decision_id) DO UPDATE SET payload_json=:payload"""),
                    {"decision_id": lineage.decision_id, "payload": json.dumps(lineage.model_dump(mode="json"), sort_keys=True)})
                self._items[lineage.decision_id] = lineage
        else:
            self._items[lineage.decision_id] = lineage

    def get(self, decision_id: str) -> dict | None:
        lineage = self._items.get(decision_id)
        if lineage is None and self.persistent:
            from sqlalchemy import text

            if self._session_factory is None:
                raise RuntimeError("LINEAGE_SESSION_FACTORY_REQUIRED")

            with self._session_factory() as db_session:
                row = db_session.execute(text("SELECT payload_json FROM decision_lineage WHERE decision_id=:decision_id"), {"decision_id": decision_id}).scalar_one_or_none()
            if row:
                lineage = DecisionLineageV1.model_validate(json.loads(row))
                self._items[decision_id] = lineage
        if lineage is None:
            return None
        value = lineage.model_dump(mode="json")
        value.pop("execution_ids", None) if not lineage.execution_ids else None
        return {"decision_id": decision_id, "nodes": value, "edges": [
            {"from": decision_id, "to": item, "type": "execution"} for item in lineage.execution_ids
        ]}

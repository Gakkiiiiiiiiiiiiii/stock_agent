"""Idempotent decision work unit with a single finalization commit boundary."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from app.domain.decision.run import DecisionRun, DecisionRunState
from app.ports.decision_repository import DecisionRepository, InMemoryDecisionRepository
from app.ports.outbox import InMemoryOutbox, Outbox


@dataclass
class DecisionUnitOfWork:
    repository: DecisionRepository
    outbox: Outbox
    run: DecisionRun | None = None
    bundle: Any | None = None
    final_result: Any | None = None
    _committed: bool = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        # Concrete SQL adapters own their transaction context; in-memory UoW
        # has no open resource. Never swallow stage failures.
        return None

    @classmethod
    def in_memory(cls) -> DecisionUnitOfWork:
        return cls(InMemoryDecisionRepository(), InMemoryOutbox())

    def receive(self, *, portfolio_id: str, idempotency_key: str, request_hash: str) -> DecisionRun:
        self.run = self.repository.get_or_create(portfolio_id=portfolio_id, idempotency_key=idempotency_key, request_hash=request_hash)
        return self.run

    def freeze_bundle(self, bundle: Any, *, bundle_id: str, bundle_hash: str, readiness_snapshot: dict | None = None) -> DecisionRun:
        if self.run is None:
            raise RuntimeError("DECISION_RUN_NOT_RECEIVED")
        if self.run.state is not DecisionRunState.RECEIVED:
            if self.run.state is DecisionRunState.BUNDLE_FROZEN and self.run.bundle_hash == bundle_hash:
                return self.run
            raise ValueError("BUNDLE_ALREADY_FROZEN")
        from dataclasses import replace
        self.bundle = bundle
        previous_version = self.run.version
        staged = replace(self.run, bundle_id=bundle_id, bundle_hash=bundle_hash,
                         readiness_snapshot=(dict(readiness_snapshot) if readiness_snapshot is not None else None))
        staged.transition(DecisionRunState.BUNDLE_FROZEN)
        self.repository.save(staged, expected_version=previous_version)
        self.run = staged
        return staged

    def advance(self, state: DecisionRunState, *, result_hash: str | None = None) -> DecisionRun:
        if self.run is None:
            raise RuntimeError("DECISION_RUN_NOT_RECEIVED")
        from dataclasses import replace
        previous_version = self.run.version
        staged = replace(self.run)
        staged.transition(state)
        if state is DecisionRunState.FORMAL_CALCULATED:
            staged.formal_result_hash = result_hash
        if state is DecisionRunState.GOVERNED:
            staged.governance_hash = result_hash
        self.repository.save(staged, expected_version=previous_version)
        self.run = staged
        return staged

    def finalize(
        self, *, final_result: Any, snapshot_id: str, event_payload: dict,
        lineage: dict | None = None, authorization: dict | None = None,
    ) -> DecisionRun:
        if self.run is None or self.run.state is not DecisionRunState.GOVERNED:
            raise ValueError("DECISION_NOT_GOVERNED")
        # The in-memory implementation models the same atomic ordering as the
        # SQL adapter: outbox is appended only after all final fields exist.
        from dataclasses import replace
        # Stage a detached copy. If outbox or persistence fails, the governed
        # run remains visible and cannot be mistaken for a finalized result.
        encoded = json.dumps(final_result, sort_keys=True, default=str, ensure_ascii=False)
        staged = replace(self.run, snapshot_id=snapshot_id, lineage_json=lineage,
                         execution_authorization_json=authorization,
                         final_response_json=final_result if isinstance(final_result, dict) else {"value": final_result},
                         final_response_hash=hashlib.sha256(encoded.encode()).hexdigest())
        staged.transition(DecisionRunState.FINALIZED)
        event_id = f"decision-finalized:{staged.decision_id}"
        previous = self.repository.get(staged.decision_id)
        try:
            self.outbox.enqueue(event_id=event_id, aggregate_id=staged.decision_id, event_type="DecisionFinalized", payload=event_payload)
            self.repository.save(staged, expected_version=self.run.version)
        except Exception:
            # Restore the two in-memory ports when a test/deterministic
            # adapter fails after one side was staged. SQL adapters rely on
            # their caller's transaction rollback for the same invariant.
            events = getattr(self.outbox, "events", None)
            if isinstance(events, dict):
                events.pop(event_id, None)
            runs = getattr(self.repository, "_runs", None)
            if isinstance(runs, dict) and previous is not None:
                runs[staged.decision_id] = previous
            raise
        self.run = staged
        self.final_result = final_result
        self._committed = True
        return self.run

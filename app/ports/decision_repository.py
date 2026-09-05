from __future__ import annotations

from typing import Protocol

from app.domain.decision.run import DecisionRun


class IdempotencyConflict(ValueError):
    """The same idempotency key was reused for different request content."""


class DecisionRepository(Protocol):
    def get_or_create(self, *, portfolio_id: str, idempotency_key: str, request_hash: str) -> DecisionRun: ...
    def save(self, run: DecisionRun, *, expected_version: int | None = None) -> DecisionRun: ...
    def get(self, decision_id: str) -> DecisionRun | None: ...


class InMemoryDecisionRepository:
    """Deterministic repository used by unit tests and local composition."""
    def __init__(self) -> None:
        from threading import RLock
        self._lock = RLock()
        self._by_key: dict[tuple[str, str], tuple[str, str]] = {}
        self._runs: dict[str, DecisionRun] = {}

    def get_or_create(self, *, portfolio_id: str, idempotency_key: str, request_hash: str) -> DecisionRun:
        import uuid
        with self._lock:
            key = (portfolio_id, idempotency_key)
            existing = self._by_key.get(key)
            if existing:
                decision_id, old_hash = existing
                if old_hash != request_hash:
                    raise IdempotencyConflict("IDEMPOTENCY_KEY_CONFLICT")
                return self._runs[decision_id]
            decision_id = str(uuid.uuid4())
            run = DecisionRun(decision_id=decision_id, request_id=str(uuid.uuid4()))
            self._by_key[key] = (decision_id, request_hash)
            self._runs[decision_id] = run
            return run

    def save(self, run: DecisionRun, *, expected_version: int | None = None) -> DecisionRun:
        with self._lock:
            old = self._runs.get(run.decision_id)
            if old is None:
                raise KeyError(run.decision_id)
            if expected_version is not None and old.version != expected_version:
                raise RuntimeError("DECISION_RUN_VERSION_CONFLICT")
            self._runs[run.decision_id] = run
            return run

    def get(self, decision_id: str) -> DecisionRun | None:
        return self._runs.get(decision_id)

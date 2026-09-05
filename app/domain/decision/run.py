"""Decision work-unit state machine independent of persistence or HTTP."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class DecisionRunState(StrEnum):
    RECEIVED = "RECEIVED"
    BUNDLE_FROZEN = "BUNDLE_FROZEN"
    SPECIALISTS_COMPLETED = "SPECIALISTS_COMPLETED"
    FORMAL_CALCULATED = "FORMAL_CALCULATED"
    GOVERNED = "GOVERNED"
    FINALIZED = "FINALIZED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


_TRANSITIONS = {
    DecisionRunState.RECEIVED: {DecisionRunState.BUNDLE_FROZEN, DecisionRunState.FAILED},
    DecisionRunState.BUNDLE_FROZEN: {DecisionRunState.SPECIALISTS_COMPLETED, DecisionRunState.FAILED},
    DecisionRunState.SPECIALISTS_COMPLETED: {DecisionRunState.FORMAL_CALCULATED, DecisionRunState.FAILED},
    DecisionRunState.FORMAL_CALCULATED: {DecisionRunState.GOVERNED, DecisionRunState.FAILED},
    DecisionRunState.GOVERNED: {DecisionRunState.FINALIZED, DecisionRunState.FAILED},
    DecisionRunState.FINALIZED: {DecisionRunState.PUBLISHED},
    DecisionRunState.PUBLISHED: set(), DecisionRunState.FAILED: set(),
}


@dataclass
class DecisionRun:
    decision_id: str
    request_id: str
    state: DecisionRunState = DecisionRunState.RECEIVED
    version: int = 0
    bundle_id: str | None = None
    bundle_hash: str | None = None
    formal_result_hash: str | None = None
    governance_hash: str | None = None
    final_response_json: dict | None = None
    final_response_hash: str | None = None
    lineage_json: dict | None = None
    execution_authorization_json: dict | None = None
    snapshot_id: str | None = None
    readiness_snapshot: dict | None = None
    last_error_code: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def transition(self, target: DecisionRunState, *, error_code: str | None = None) -> None:
        if target not in _TRANSITIONS[self.state]:
            raise ValueError(f"INVALID_DECISION_TRANSITION:{self.state}->{target}")
        self.state = target
        self.version += 1
        self.last_error_code = error_code
        self.updated_at = datetime.now(UTC)

    @property
    def execution_eligible(self) -> bool:
        return self.state is DecisionRunState.FINALIZED and not self.last_error_code

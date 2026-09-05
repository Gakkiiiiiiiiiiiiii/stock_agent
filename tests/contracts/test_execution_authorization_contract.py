from datetime import UTC, datetime, timedelta

import pytest

from app.domain.decision.execution_authorization import (
    ExecutionAuthorizationEnvelope,
    FormalDecisionFinalizer,
)
from app.domain.decision.run import DecisionRun, DecisionRunState


def test_serialized_envelope_contract_is_machine_readable():
    decision = DecisionRun("d", "r")
    for state in (DecisionRunState.BUNDLE_FROZEN, DecisionRunState.SPECIALISTS_COMPLETED,
                  DecisionRunState.FORMAL_CALCULATED, DecisionRunState.GOVERNED,
                  DecisionRunState.FINALIZED):
        decision.transition(state)
    lineage = {"trace_id": "t", "request_id": "r", "decision_bundle_id": "b", "decision_id": "d",
               "decision_snapshot_id": "s", "market_snapshot_id": "m", "factor_artifact_id": "f",
               "content_snapshot_id": "c", "portfolio_id": "p", "policy_version": "v", "producer_commit": "g"}
    issued = FormalDecisionFinalizer().finalize(decision=decision, snapshot_id="s", governance=type("G", (), {"approved": True})(), policy_version="v", lineage=lineage, valid_until=datetime.now(UTC) + timedelta(minutes=1), portfolio_id="p", contract_checksums={"formal": "sha256:abc"})
    with pytest.raises(TypeError, match="EXECUTION_AUTHORIZATION_FINALIZER_ONLY"):
        ExecutionAuthorizationEnvelope.model_validate(issued.model_dump())
    assert issued.contract == "execution-authorization.v1"
    assert issued.authority == "FORMAL"

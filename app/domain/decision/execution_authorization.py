"""The sole execution-authorization construction boundary."""
from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

_FINALIZER_CONTEXT: ContextVar[bool] = ContextVar("formal_authorization_finalizer", default=False)


class ExecutionAuthorizationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract: str = "execution-authorization.v1"
    envelope_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    decision_snapshot_id: str = Field(min_length=1)
    portfolio_id: str = Field(min_length=1)
    allowed_actions: tuple[str, ...]
    max_notional: Decimal = Field(ge=0)
    valid_until: datetime
    contract_checksums: dict[str, str]
    authority: str = "FORMAL"
    _factory_token: ClassVar[object] = object()

    def __init__(self, **data: Any) -> None:
        if not _FINALIZER_CONTEXT.get():
            raise TypeError("EXECUTION_AUTHORIZATION_FINALIZER_ONLY")
        super().__init__(**data)

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> ExecutionAuthorizationEnvelope:
        if not _FINALIZER_CONTEXT.get():
            raise TypeError("EXECUTION_AUTHORIZATION_FINALIZER_ONLY")
        return super().model_construct(_fields_set=_fields_set, **values)

    @model_validator(mode="after")
    def validate_envelope(self) -> ExecutionAuthorizationEnvelope:
        if self.contract != "execution-authorization.v1" or self.authority != "FORMAL":
            raise ValueError("FORMAL_AUTHORIZATION_REQUIRED")
        if self.valid_until <= datetime.now(UTC):
            raise ValueError("AUTHORIZATION_EXPIRED")
        if not self.allowed_actions:
            raise ValueError("ALLOWED_ACTIONS_REQUIRED")
        if not self.contract_checksums or any(not value.startswith("sha256:") for value in self.contract_checksums.values()):
            raise ValueError("CONTRACT_CHECKSUM_REQUIRED")
        return self

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> ExecutionAuthorizationEnvelope:
        if update:
            raise TypeError("EXECUTION_AUTHORIZATION_IMMUTABLE")
        return super().model_copy(deep=deep)


class FormalDecisionFinalizer:
    """Validate and issue formal output and authorization in one boundary."""

    def finalize(self, *, decision: Any, snapshot_id: str, governance: Any,
                 policy_version: str, lineage: dict[str, Any], valid_until: datetime,
                 portfolio_id: str, contract_checksums: dict[str, str],
                 allowed_actions: list[str] | tuple[str, ...] = ("HOLD",),
                 max_notional: Decimal = Decimal(0), strategy_version: str | None = None) -> ExecutionAuthorizationEnvelope:
        state = (decision.get("state") or decision.get("status")) if isinstance(decision, dict) else (getattr(decision, "state", None) or getattr(decision, "status", None))
        if str(state).upper() != "FINALIZED":
            raise ValueError("DECISION_NOT_FINALIZED")
        # Authorization is an affirmative capability, never a generic
        # finalized-decision marker.  Keep the legacy DecisionRun-only test
        # shape compatible when it has no action field, but enforce the
        # decision's explicit action/status whenever one is present.
        action = None
        if isinstance(decision, dict):
            action = decision.get("investment_action") or decision.get("action") or decision.get("status")
        else:
            action = (getattr(decision, "investment_action", None)
                      or getattr(decision, "action", None)
                      or getattr(decision, "status", None))
        if action is not None:
            normalized = str(action).upper()
            if normalized in {"HOLD", "WATCH", "VETO", "REJECTED", "REJECT"}:
                raise ValueError("EXECUTION_NOT_ELIGIBLE")
            if normalized not in {"APPROVED", "APPROVE", "APPROVE_WITH_ADJUSTMENT", "BUY", "SELL"}:
                raise ValueError("EXECUTION_ACTION_NOT_APPROVED")
            if not allowed_actions or all(str(item).upper() in {"HOLD", "WATCH", "VETO"} for item in allowed_actions):
                raise ValueError("ALLOWED_ACTIONS_REQUIRED")
            if max_notional <= 0:
                raise ValueError("POSITIVE_NOTIONAL_REQUIRED")
        if not snapshot_id:
            raise ValueError("DECISION_SNAPSHOT_REQUIRED")
        approved = governance.get("approved") if isinstance(governance, dict) else getattr(governance, "approved", None)
        if approved is not True:
            raise ValueError("GOVERNANCE_REQUIRED")
        if not policy_version:
            raise ValueError("POLICY_VERSION_REQUIRED")
        if strategy_version is not None and not strategy_version.strip():
            raise ValueError("STRATEGY_VERSION_REQUIRED")
        if lineage.get("policy_version") != policy_version:
            raise ValueError("POLICY_LINEAGE_MISMATCH")
        required = {"trace_id", "request_id", "decision_bundle_id", "decision_id", "decision_snapshot_id",
                    "market_snapshot_id", "factor_artifact_id", "content_snapshot_id", "portfolio_id",
                    "policy_version", "producer_commit"}
        if not required.issubset(lineage):
            raise ValueError("LINEAGE_REQUIRED")
        if valid_until.tzinfo is None or valid_until.utcoffset() is None:
            raise ValueError("VALID_UNTIL_MUST_BE_TIMEZONE_AWARE")
        if valid_until <= datetime.now(UTC):
            raise ValueError("AUTHORIZATION_EXPIRED")
        if str(lineage.get("decision_snapshot_id")) != snapshot_id:
            raise ValueError("SNAPSHOT_LINEAGE_MISMATCH")
        if str(lineage.get("portfolio_id")) != portfolio_id:
            raise ValueError("PORTFOLIO_LINEAGE_MISMATCH")
        values = {
            "envelope_id": str(uuid4()),
            "contract": "execution-authorization.v1",
            "authority": "FORMAL",
            "decision_id": str((decision.get("decision_id") if isinstance(decision, dict) else getattr(decision, "decision_id", None)) or lineage["decision_id"]),
            "decision_snapshot_id": snapshot_id,
            "portfolio_id": portfolio_id,
            "allowed_actions": tuple(allowed_actions),
            "max_notional": max_notional,
            "valid_until": valid_until,
            "contract_checksums": contract_checksums,
        }
        marker = _FINALIZER_CONTEXT.set(True)
        try:
            return ExecutionAuthorizationEnvelope(**values)
        finally:
            _FINALIZER_CONTEXT.reset(marker)

    # Descriptive aliases keep callers from constructing the envelope model
    # directly while allowing either command-oriented naming convention.
    create = finalize
    create_envelope = finalize

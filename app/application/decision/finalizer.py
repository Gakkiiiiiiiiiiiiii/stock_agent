from __future__ import annotations

from typing import Any

from app.domain.decision.execution_authorization import FormalDecisionFinalizer


class DecisionFinalizer:
    """Application façade that delegates issuance to the sole domain finalizer."""
    def __init__(self, authorization_finalizer: FormalDecisionFinalizer | None = None):
        self.authorization_finalizer = authorization_finalizer or FormalDecisionFinalizer()

    def authorize(self, **kwargs: Any) -> Any:
        return self.authorization_finalizer.finalize(**kwargs)

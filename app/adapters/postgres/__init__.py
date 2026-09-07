"""Postgres adapters with profile-safe lazy exports."""
from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["PostgresDecisionOutbox", "PostgresDecisionRepository", "PostgresKnowledgeConclusionRepository"]

if TYPE_CHECKING:
    from .decision_outbox import PostgresDecisionOutbox
    from .decision_repository import PostgresDecisionRepository
    from .knowledge_conclusion_repository import PostgresKnowledgeConclusionRepository


def __getattr__(name: str) -> object:
    """Avoid importing formal decision adapters when only conclusions are used."""
    if name == "PostgresDecisionOutbox":
        from .decision_outbox import PostgresDecisionOutbox

        value = PostgresDecisionOutbox
    elif name == "PostgresDecisionRepository":
        from .decision_repository import PostgresDecisionRepository

        value = PostgresDecisionRepository
    elif name == "PostgresKnowledgeConclusionRepository":
        from .knowledge_conclusion_repository import (
            PostgresKnowledgeConclusionRepository,
        )

        value = PostgresKnowledgeConclusionRepository
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value

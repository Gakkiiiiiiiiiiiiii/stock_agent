"""Profile-selected shared services for route handlers.

Profile resolution intentionally happens before importing the formal composition
root so a knowledge-only process does not need its formal modules or clients.
"""
from __future__ import annotations

from app.domain.capability_profile import CapabilityProfile, resolve_capability_profile

capability_profile = resolve_capability_profile()


def init_application() -> None:
    """Verify the schema only; migrations are owned by scripts.migrate_schema."""

    from storage.bootstrap import verify_schema

    verify_schema()


def configure_trading_clock(*, offline: bool = False) -> None:
    """Compatibility facade for FULL-profile clock configuration.

    Importing this name must not import the formal composition in a
    knowledge-only process, so the legacy helper remains lazy.
    """

    from app.composition.formal_decision_composition import (
        configure_trading_clock as configure,
    )

    configure(offline=offline)


if capability_profile is CapabilityProfile.KNOWLEDGE_ONLY:
    from app.composition.knowledge_composition import build_knowledge_components

    _components = build_knowledge_components()
    content_client = _components.content_client
    content_bundle_client = _components.content_bundle_client
    knowledge_conclusion_repository = _components.repository
    knowledge_conclusion_run_service = _components.run_service
    knowledge_conclusion_lineage_service = _components.lineage_service
    knowledge_conclusion_model = _components.model
else:
    from app.composition.formal_decision_composition import (
        build_formal_decision_components,
    )

    _components = build_formal_decision_components()
    content_client = _components.content_client
    factor_client = _components.factor_client
    quant_client = _components.quant_client
    orchestrator = _components.orchestrator
    admin_service = _components.admin_service
    chat_history_service = _components.chat_history_service
    lineage_graph = _components.lineage_graph
    _shared_clock = _components.shared_clock

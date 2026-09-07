"""Knowledge-only composition with no formal-decision dependencies."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapters.http.fixed_route_structured_model import FixtureStructuredModel
from app.adapters.local.unavailable_structured_model import UnavailableStructuredModel
from app.adapters.postgres.knowledge_conclusion_repository import (
    PostgresKnowledgeConclusionRepository,
)
from app.application.knowledge_conclusion.lineage import (
    KnowledgeConclusionLineageService,
)
from app.application.knowledge_conclusion.run_service import (
    KnowledgeConclusionRunService,
)
from app.ports.content_knowledge import ContentKnowledgeBundlePort
from app.ports.knowledge_conclusion_model import KnowledgeConclusionStructuredModel
from services.subsystems import get_content_client, get_content_knowledge_bundle_client


@dataclass(frozen=True)
class KnowledgeComponents:
    """Dependencies allowed before the conclusion feature is installed."""

    content_client: Any
    content_bundle_client: ContentKnowledgeBundlePort
    repository: PostgresKnowledgeConclusionRepository
    run_service: KnowledgeConclusionRunService
    lineage_service: KnowledgeConclusionLineageService
    model: KnowledgeConclusionStructuredModel


def build_knowledge_components() -> KnowledgeComponents:
    # Client construction is URL-only and performs no network probe. Formal
    # decision clients, the trading clock, and broker paths never enter here.
    repository = PostgresKnowledgeConclusionRepository()
    run_service = KnowledgeConclusionRunService(repository)
    return KnowledgeComponents(
        content_client=get_content_client(),
        content_bundle_client=get_content_knowledge_bundle_client(),
        repository=repository,
        run_service=run_service,
        lineage_service=KnowledgeConclusionLineageService(repository),
        model=_structured_model(),
    )


def _structured_model() -> KnowledgeConclusionStructuredModel:
    """Select only an explicit, fixed fixture binding; no ambient provider."""
    if os.getenv("KNOWLEDGE_CONCLUSION_MODEL_ADAPTER", "").strip() != "fixture":
        return UnavailableStructuredModel()
    credential_file = os.getenv("KNOWLEDGE_CONCLUSION_MODEL_API_KEY_FILE", "").strip()
    endpoint = os.getenv("KNOWLEDGE_CONCLUSION_FIXTURE_MODEL_URL", "").strip()
    if not credential_file or not endpoint:
        return UnavailableStructuredModel()
    try:
        if not Path(credential_file).read_text(encoding="utf-8").strip():
            return UnavailableStructuredModel()
    except OSError:
        return UnavailableStructuredModel()
    try:
        return FixtureStructuredModel(
            endpoint=endpoint,
            credential_file=Path(credential_file),
        )
    except (TypeError, ValueError):
        return UnavailableStructuredModel()

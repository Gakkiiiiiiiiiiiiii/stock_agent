"""Readiness policy for the isolated content-only conclusion surface."""
from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.domain.capability_profile import CapabilityProfile, resolve_capability_profile
from app.ports.content_knowledge import CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM
from storage.db import session_scope


class KnowledgeConclusionReadiness:
    """Real probes by default; tests may inject each probe explicitly."""
    def __init__(self, *, probes: dict[str, Callable[[], str]] | None = None) -> None:
        self._probes = probes or {}

    def report(self) -> tuple[bool, dict[str, str]]:
        checks = {
            "profile": "ok" if resolve_capability_profile() is CapabilityProfile.KNOWLEDGE_ONLY else "failed",
            "schema": self._probe("schema", self._schema),
            "repository": self._probe("repository", self._repository),
            "content_service": self._probe("content_service", self._content_service),
            "content_contract": self._probe("content_contract", self._content_contract),
            "model": self._probe("model", self._model),
            "fallback": "enabled" if _fallback_enabled() else "disabled",
            "clock": "ok" if _utc_aware() else "failed",
            "auth_policy": _auth_policy(),
        }
        ready = all(checks[name] == "ok" for name in ("profile", "schema", "repository", "content_service", "content_contract", "clock"))
        ready = ready and (checks["model"] == "ok" or checks["fallback"] == "enabled")
        return ready, checks

    def _probe(self, name: str, default: Callable[[], str]) -> str:
        try:
            return self._probes.get(name, default)()
        except (httpx.HTTPError, OSError, RuntimeError, SQLAlchemyError):
            return "failed"

    @staticmethod
    def _schema() -> str:
        with session_scope() as session:
            versions = {row[0] for row in session.execute(text("SELECT version FROM schema_migration"))}
        return "ok" if {"042_knowledge_conclusion_runs.sql", "043_knowledge_conclusion_lineage_audit.sql"} <= versions else "failed"

    @staticmethod
    def _repository() -> str:
        # The zero-row update proves the configured role can write this table
        # without fabricating a readiness record or mutating a real run.
        with session_scope() as session:
            session.execute(text("UPDATE knowledge_conclusion_run SET updated_at=updated_at WHERE 1=0"))
        return "ok"

    @staticmethod
    def _content_health() -> dict[str, object]:
        base = os.getenv("CONTENT_SERVICE_URL", "http://stock-content:8100").rstrip("/")
        response = httpx.get(f"{base}/health/knowledge-bundle-ready", timeout=3.0)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _content_service(self) -> str:
        payload = self._content_health()
        return "ok" if payload.get("status") in {"ready", "ok"} or payload.get("ready") is True else "failed"

    def _content_contract(self) -> str:
        payload = self._content_health()
        checksum = _content_contract_checksum(payload)
        return "ok" if checksum == CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM else "failed"

    @staticmethod
    def _model() -> str:
        # This is intentionally composition-derived, not a deployment claim.
        # An environment flag cannot turn an unavailable adapter into a model.
        from app.dependencies import knowledge_conclusion_model

        probe = getattr(knowledge_conclusion_model, "probe_ready", None)
        if callable(probe):
            return "ok" if probe() is True else "degraded"
        return "ok" if getattr(knowledge_conclusion_model, "is_available", False) is True else "degraded"


def _content_contract_checksum(payload: dict[str, object]) -> str | None:
    """Accept only a producer-provided checksum value, never a ready boolean.

    New Content readiness exposes the checksum at top level.  The nested form
    supports an already-deployed component envelope only when that component
    carries the exact checksum itself; ``ready=true`` is insufficient proof.
    """
    direct = payload.get("contract_checksum")
    if isinstance(direct, str):
        return direct
    producer = payload.get("producer")
    if isinstance(producer, dict) and isinstance(producer.get("contract_checksum"), str):
        return producer["contract_checksum"]
    components = payload.get("components")
    if not isinstance(components, dict):
        return None
    component = components.get("contract_checksum")
    if not isinstance(component, dict):
        return None
    for key in ("checksum", "value", "actual_checksum"):
        candidate = component.get(key)
        if isinstance(candidate, str):
            return candidate
    return None


def _fallback_enabled() -> bool:
    return os.getenv("KNOWLEDGE_CONCLUSION_DETERMINISTIC_FALLBACK", "true").strip().lower() in {"1", "true", "yes"}


def _utc_aware() -> bool:
    now = datetime.now(UTC)
    return now.tzinfo is not None and now.utcoffset() is not None


def _auth_policy() -> str:
    mode = os.getenv("STOCK_AGENT_API_AUTH_MODE", "none").strip().lower()
    return mode if mode in {"none", "bearer"} else "invalid"

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engines.content.knowledge_conflict_resolver import KnowledgeConflictResolver


def _unit(kind: str, sentiment: str, *, newer: bool, condition_text: str | None = None) -> dict:
    suffix = "new" if newer else "old"
    return {
        "knowledge_uid": f"{kind.lower()}-{suffix}",
        "knowledge_kind": kind,
        "sentiment": sentiment,
        "conflict_key": f"MARKET|{kind}|券商|state|",
        "lifecycle_status": "ACTIVE",
        "verification_status": "SOURCE_CONFIRMED",
        "as_of_time": datetime.now(UTC) + (timedelta(days=1) if newer else timedelta(days=0)),
        "condition_text": condition_text,
    }


def test_method_conflict_does_not_auto_supersede():
    units, relations = KnowledgeConflictResolver().resolve([_unit("METHOD", "BEARISH", newer=False), _unit("METHOD", "BULLISH", newer=True)])

    assert {unit["lifecycle_status"] for unit in units} == {"ACTIVE"}
    assert relations[0]["relation_type"] == "CONFLICTS_WITH"
    assert relations[0]["attributes"]["recommended_action"] == "manual_review_before_supersede"
    assert {unit["verification_status"] for unit in units} == {"NEEDS_REVIEW"}


def test_concept_conflict_does_not_auto_supersede():
    units, relations = KnowledgeConflictResolver().resolve([_unit("CONCEPT", "BEARISH", newer=False), _unit("CONCEPT", "BULLISH", newer=True)])

    assert {unit["lifecycle_status"] for unit in units} == {"ACTIVE"}
    assert relations[0]["relation_type"] == "CONFLICTS_WITH"


def test_forecast_conflict_keeps_history():
    units, relations = KnowledgeConflictResolver().resolve([_unit("FORECAST", "BEARISH", newer=False), _unit("FORECAST", "BULLISH", newer=True)])

    assert {unit["lifecycle_status"] for unit in units} == {"ACTIVE"}
    assert relations[0]["relation_type"] == "CONFLICTS_WITH"
    assert relations[0]["attributes"]["recommended_action"] == "keep_forecast_history"


def test_fact_conflict_requires_review_instead_of_supersede():
    units, relations = KnowledgeConflictResolver().resolve([_unit("FACT", "BEARISH", newer=False), _unit("FACT", "BULLISH", newer=True)])

    assert {unit["lifecycle_status"] for unit in units} == {"ACTIVE"}
    assert relations[0]["relation_type"] == "CONFLICTS_WITH"
    assert relations[0]["attributes"]["recommended_action"] == "require_evidence_verification"


def test_state_conflict_supersedes_older_state():
    units, relations = KnowledgeConflictResolver().resolve([_unit("STATE", "BEARISH", newer=False), _unit("STATE", "BULLISH", newer=True)])

    statuses_by_uid = {unit["knowledge_uid"]: unit["lifecycle_status"] for unit in units}
    assert statuses_by_uid["state-old"] == "SUPERSEDED"
    assert statuses_by_uid["state-new"] == "ACTIVE"
    assert relations[0]["relation_type"] == "SUPERSEDES"


def test_action_conflict_marks_review_when_no_explicit_invalidation():
    units, relations = KnowledgeConflictResolver().resolve([_unit("ACTION", "BEARISH", newer=False), _unit("ACTION", "BULLISH", newer=True)])

    assert {unit["lifecycle_status"] for unit in units} == {"ACTIVE"}
    assert {unit["verification_status"] for unit in units} == {"NEEDS_REVIEW"}
    assert relations[0]["relation_type"] == "CONFLICTS_WITH"


def test_action_conflict_supersedes_when_condition_is_explicit():
    units, relations = KnowledgeConflictResolver().resolve(
        [_unit("ACTION", "BEARISH", newer=False), _unit("ACTION", "BULLISH", newer=True, condition_text="若放量突破则加仓")]
    )

    statuses_by_uid = {unit["knowledge_uid"]: unit["lifecycle_status"] for unit in units}
    assert statuses_by_uid["action-old"] == "SUPERSEDED"
    assert relations[0]["relation_type"] == "SUPERSEDES"

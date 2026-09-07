"""knowledge-conclusion.v1 schema coverage."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from app.domain.knowledge_conclusion import ConclusionVerdict
from tests.test_knowledge_conclusion import _conclusion

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "knowledge-conclusion.v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("verdict", list(ConclusionVerdict))
def test_schema_accepts_each_supported_verdict(verdict: ConclusionVerdict) -> None:
    jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER).validate(
        _conclusion(verdict=verdict).model_dump(mode="json")
    )


def test_schema_rejects_execution_or_action_text() -> None:
    payload = _conclusion().model_dump(mode="json")
    payload["execution_eligible"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)

    payload = _conclusion().model_dump(mode="json")
    payload["summary"] = "BUY"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)


@pytest.mark.parametrize("phrase", (
    "go long", "open a position", "stop loss", "take profit", "target price",
    "建仓", "平仓", "做空", "止盈", "目标价",
    "现在入场", "立即离场", "建议清仓", "enter now", "exit now",
    "close your long", "set a stop at 10", "take profits now",
))
def test_schema_portably_rejects_coarse_trade_instruction_terms(phrase: str) -> None:
    payload = _conclusion().model_dump(mode="json")
    payload["summary"] = phrase

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)


@pytest.mark.parametrize("field", ("query", "summary", "findings", "conditions", "risks", "contradictions", "limitations"))
def test_schema_applies_the_portable_action_guard_to_every_visible_field(field: str) -> None:
    payload = _conclusion().model_dump(mode="json")
    phrase = "现在入场"
    if field == "findings":
        payload[field][0]["text"] = phrase
    elif field == "query" or field == "summary":
        payload[field] = phrase
    else:
        payload[field] = [phrase]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)

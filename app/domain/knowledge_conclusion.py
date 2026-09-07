"""Pure, content-bound research conclusion types.

This module deliberately cannot turn content research into a trading action.
Every displayed narrative value is selected from a validated finding, which
keeps the conclusion inside the supplied evidence boundary until a later
integration packet supplies bundle freezing and model orchestration.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# This remains deliberately small and explicit.  It is a *research-output*
# gate, not a generic financial-language filter: facts such as a company
# holding cash and conditional risk statements must remain expressible.
_ENGLISH_ACTION_PATTERNS = (
    r"\bb\s*u\s*y\b",
    r"\bs\s*e\s*l\s*l\b",
    r"\bh\s*o\s*l\s*d\b",
    r"\bgo\s+long\b",
    r"\bgo\s+short\b",
    # Position verbs need their object (or an unmistakable imperative time
    # marker) so a reported fact such as "market participants are entering a
    # market" remains research text.  The direct reader-facing forms below
    # are deliberately fail-closed.
    r"\b(?:open|close|enter|exit|clear|liquidat(?:e|es|ed|ing)|hold)\s+(?:a\s+|the\s+|your\s+)?(?:long|short|position|positions|trade|trades|exposure|holding|holdings)\b",
    r"\b(?:enter|exit|open|close|clear|liquidat(?:e|es|ed|ing)|hold)\s+(?:now|immediately|today)\b",
    r"\b(?:increase|decrease|add|reduce|trim)\s+(?:your\s+|the\s+)?(?:position|positions|exposure|holding|holdings)\b",
    r"\bstop\s*loss\b",
    r"\btake\s*profit\b",
    r"\btake\s+profits?\s+(?:now|immediately|today)\b",
    r"\b(?:set|place|move|use|add)\s+(?:a\s+|the\s+|your\s+)?(?:stop|stop\s*loss|take\s*profit|profit\s*taking|target(?:\s*price)?)\b",
    r"\bstop\s+(?:at|below|above)\b",
    r"\b(?:target\s*price|price\s*target)\b",
    r"\brecommend(?:ed|ation)?\s+(?:that\s+)?(?:you\s+|investors?\s+)?(?:buy|buying|sell|selling|hold|holding|go\s+long|go\s+short|enter|entering|exit|exiting|open|close|clear|liquidat(?:e|ing))\b",
    r"\b(?:order\s*command|execution\s*authorization|broker)\b",
)
_ENGLISH_ACTION_RE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _ENGLISH_ACTION_PATTERNS)
_CHINESE_DIRECT_ACTION_RE = re.compile(
    r"买入|卖出|建仓|开仓|平仓|清仓|加仓|减仓|补仓|做多|做空|入场|离场|进场|出场|"
    r"退出交易|仓位指令|止损|止盈|止赚|目标价|目标价格|"
    r"推荐(?:买入|卖出|建仓|开仓|平仓|清仓|加仓|减仓|补仓|做多|做空|入场|离场|进场|出场)",
)
_CHINESE_HOLD_SAFE_SUFFIXES = ("现金", "资产", "股权", "股份", "债券", "物业", "数量", "比例")
_INVISIBLE_OR_FORMATTING_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_SEPARATOR_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConclusionVerdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    MIXED = "MIXED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ModelIdentity(_FrozenModel):
    """Lineage of the future structured-model/fallback construction path."""

    mode: Literal["MODEL", "FALLBACK"]
    provider: str
    model: str
    prompt_version: Literal["knowledge-conclusion.prompt.v1"] = "knowledge-conclusion.prompt.v1"

    @field_validator("provider", "model")
    @classmethod
    def _identity_is_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model identity must be nonblank")
        return value


def _normalized_action_text(value: str) -> tuple[str, str]:
    """Return a Unicode-normalized display form and punctuation-neutral form."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _INVISIBLE_OR_FORMATTING_RE.sub("", normalized).strip()
    # Punctuation and full-width variants cannot be used to evade an action
    # phrase (for example ``B．U．Y`` or ``止-损``).  Keep spaces so English
    # word boundaries still distinguish ``holding`` from ``hold``.
    separated = _SEPARATOR_RE.sub(" ", normalized.casefold()).replace("_", " ").strip()
    return normalized, separated


def _contains_chinese_hold_instruction(compact: str) -> bool:
    """Treat 持有 as an instruction only outside clearly factual noun phrases."""
    start = 0
    while True:
        index = compact.find("持有", start)
        if index < 0:
            return False
        suffix = compact[index + len("持有"):]
        # ``公司持有现金`` and ``长期持有资产的股东数量`` are descriptive
        # facts, not a reader instruction.  Other bare/position uses fail
        # closed because this contract is content research, not advice.
        if not suffix.startswith(_CHINESE_HOLD_SAFE_SUFFIXES):
            return True
        start = index + len("持有")


def prohibited_action_language(value: str) -> str | None:
    """Classify normalized direct trading/position instructions, if any.

    Domain validation is authoritative because JSON Schema cannot portably do
    Unicode normalization or recursively inspect future visible fields.
    """
    normalized, separated = _normalized_action_text(value)
    if any(pattern.search(separated) for pattern in _ENGLISH_ACTION_RE):
        return "trading or execution instruction"
    compact = separated.replace(" ", "")
    if _CHINESE_DIRECT_ACTION_RE.search(compact) or _contains_chinese_hold_instruction(compact):
        return "trading or position instruction"
    # Preserve original nonblank validation behavior while making the
    # normalized value available to the classifier above.
    return None if normalized else "blank"


def reject_action_language(value: str, *, field_name: str) -> str:
    """Validate one displayed string through the centralized action classifier."""
    normalized, _ = _normalized_action_text(value)
    if not normalized:
        raise ValueError(f"{field_name} must be nonblank")
    reason = prohibited_action_language(normalized)
    if reason:
        raise ValueError(f"{field_name} contains prohibited action language: {reason}")
    # Classification normalizes for matching only.  Preserve the caller's
    # valid display text (apart from surrounding whitespace) in frozen output.
    return value.strip()


def reject_action_language_recursively(value: object, *, field_name: str) -> object:
    """Apply the same gate to every displayed string in a nested value.

    The v1 conclusion currently exposes scalar narratives and finding text,
    but keeping the traversal here prevents a future visible labels/metadata
    field from accidentally bypassing the sole classifier.
    """
    if isinstance(value, str):
        return reject_action_language(value, field_name=field_name)
    if isinstance(value, Mapping):
        return {
            str(key): reject_action_language_recursively(item, field_name=f"{field_name}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(reject_action_language_recursively(item, field_name=field_name) for item in value)
    if isinstance(value, list):
        return [reject_action_language_recursively(item, field_name=field_name) for item in value]
    return value


def _nonblank_identifier(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must be nonblank")
    return value


class Finding(_FrozenModel):
    text: str
    knowledge_ids: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("text")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return reject_action_language(value, field_name="finding.text")

    @field_validator("knowledge_ids", "evidence_ids")
    @classmethod
    def _validated_citations(cls, values: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "citation ids")
        cleaned = tuple(_nonblank_identifier(value, field_name) for value in values)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError(f"{field_name} must be unique")
        return cleaned


class KnowledgeConclusionRequest(_FrozenModel):
    """Explicit clocks are hashable input data; this layer never reads a clock."""

    content_snapshot_id: str
    query: str
    symbol: str | None = None
    business_as_of: datetime | None = None
    knowledge_as_of: datetime | None = None
    availability_as_of: datetime | None = None

    @field_validator("content_snapshot_id")
    @classmethod
    def _snapshot_is_nonblank(cls, value: str) -> str:
        return _nonblank_identifier(value, "content_snapshot_id")

    @field_validator("query")
    @classmethod
    def _query_is_safe(cls, value: str) -> str:
        return reject_action_language(value, field_name="query")

    @field_validator("symbol")
    @classmethod
    def _symbol_is_nonblank(cls, value: str | None) -> str | None:
        return _nonblank_identifier(value, "symbol") if value is not None else None


class KnowledgeConclusion(_FrozenModel):
    contract: Literal["knowledge-conclusion.v1"] = "knowledge-conclusion.v1"
    conclusion_id: str
    scope: Literal["CONTENT_ONLY_RESEARCH"] = "CONTENT_ONLY_RESEARCH"
    execution_eligible: Literal[False] = False
    query: str
    content_bundle_id: str
    content_snapshot_id: str
    verdict: ConclusionVerdict
    market_stance: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNCERTAIN"]
    summary: str
    findings: tuple[Finding, ...] = Field(min_length=1)
    conditions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    model: ModelIdentity
    created_at: datetime

    @field_validator("conclusion_id", "content_bundle_id", "content_snapshot_id")
    @classmethod
    def _identifiers_are_nonblank(cls, value: str, info: object) -> str:
        return _nonblank_identifier(value, getattr(info, "field_name", "identifier"))

    @field_validator("query", "summary")
    @classmethod
    def _display_text_is_safe(cls, value: str, info: object) -> str:
        return reject_action_language(value, field_name=getattr(info, "field_name", "text"))

    @field_validator("conditions", "risks", "contradictions", "limitations")
    @classmethod
    def _display_lists_are_safe(cls, values: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "text")
        return tuple(reject_action_language(value, field_name=field_name) for value in values)

    @field_validator("created_at")
    @classmethod
    def _created_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _all_displayed_facts_are_grounded(self) -> KnowledgeConclusion:
        # Keep one recursive, centralized closure over every string that the
        # public v1 payload displays.  Field validators above provide precise
        # input errors; this protects any construction/replay path that grows
        # a nested visible shape without adding a separate classifier.
        reject_action_language_recursively(
            {
                "query": self.query,
                "summary": self.summary,
                "findings": tuple({"text": finding.text} for finding in self.findings),
                "conditions": self.conditions,
                "risks": self.risks,
                "contradictions": self.contradictions,
                "limitations": self.limitations,
            },
            field_name="knowledge_conclusion",
        )
        finding_texts = {finding.text for finding in self.findings}
        displayed = (self.summary, *self.conditions, *self.risks, *self.contradictions, *self.limitations)
        ungrounded = [value for value in displayed if value not in finding_texts]
        if ungrounded:
            raise ValueError("displayed narrative must be selected from a cited finding")
        return self

    @classmethod
    def construct(
        cls,
        *,
        request: KnowledgeConclusionRequest,
        conclusion_id: str,
        content_bundle_id: str,
        verdict: ConclusionVerdict,
        market_stance: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNCERTAIN"],
        summary: Finding,
        findings: Iterable[Finding],
        conditions: Iterable[Finding] = (),
        risks: Iterable[Finding] = (),
        contradictions: Iterable[Finding] = (),
        limitations: Iterable[Finding] = (),
        model: ModelIdentity,
        created_at: datetime,
    ) -> KnowledgeConclusion:
        """Build only from validated findings, never free-text model output."""
        all_findings = tuple(findings)
        if summary not in all_findings:
            all_findings = (summary, *all_findings)
        selected = {
            "conditions": tuple(conditions),
            "risks": tuple(risks),
            "contradictions": tuple(contradictions),
            "limitations": tuple(limitations),
        }
        for values in selected.values():
            if any(finding not in all_findings for finding in values):
                raise ValueError("displayed finding must be included in findings")
        return cls(
            conclusion_id=conclusion_id,
            query=request.query,
            content_bundle_id=content_bundle_id,
            content_snapshot_id=request.content_snapshot_id,
            verdict=verdict,
            market_stance=market_stance,
            summary=summary.text,
            findings=all_findings,
            conditions=tuple(finding.text for finding in selected["conditions"]),
            risks=tuple(finding.text for finding in selected["risks"]),
            contradictions=tuple(finding.text for finding in selected["contradictions"]),
            limitations=tuple(finding.text for finding in selected["limitations"]),
            model=model,
            created_at=created_at,
        )


def revalidate_public_conclusion(value: KnowledgeConclusion | object) -> KnowledgeConclusion | object:
    """Reapply the current display-language gate before returning stored data.

    Runs written under an older vocabulary may still deserialize as frozen
    Pydantic objects.  Public GET, idempotent POST, and lineage must not make
    such a historical record a bypass when the deny list is tightened.
    """
    if not isinstance(value, KnowledgeConclusion):
        # Repository adapters are typed to return KnowledgeConclusion.  Keep
        # thin route-test doubles and non-domain error envelopes outside this
        # domain invariant rather than accepting a partial mapping as a real
        # stored conclusion.
        return value
    return KnowledgeConclusion.model_validate(value.model_dump(mode="python"))

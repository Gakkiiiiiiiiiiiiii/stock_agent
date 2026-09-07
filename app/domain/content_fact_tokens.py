"""Conservative hard-fact extraction for content-bound conclusions.

This is intentionally a lexical guard, rather than an entity recognizer.  It
only returns facts whose surface form can be compared with frozen content.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

_NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})*(?:\.\d+)?(?![\w.])")
_CHINESE_NUMBER = re.compile(r"[零〇一二三四五六七八九十百千万亿两]+")
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|百分点)")
_CURRENCY = re.compile(r"(?:[$¥￥€£]|USD|CNY|RMB|人民币)\s*\d+(?:\.\d+)?(?:\s*(?:万|亿|million|billion))?", re.IGNORECASE)
_DATE = re.compile(r"\d{4}(?:[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?|年?\s*Q[1-4]|年?\s*[一二三四1-4]季度)?", re.IGNORECASE)
_TICKER = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{1,5}|\d{6})(?![A-Za-z0-9])")
_COMPARISON = re.compile(r"同比|环比|高于|低于|超过|不及|greater than|less than|versus|vs\.?", re.IGNORECASE)
_DIRECTION = re.compile(r"增长|上升|提高|扩大|下降|下滑|减少|收缩|increase(?:d)?|decrease(?:d)?|rise|fall", re.IGNORECASE)


def _implicit_entities(text: str) -> set[str]:
    """Extract short company/product surface forms without an entity service."""
    found: set[str] = set()
    for suffix in ("公司", "集团", "股份"):
        offset = 0
        while (index := text.find(suffix, offset)) >= 0:
            start = index
            while start and "\u4e00" <= text[start - 1] <= "\u9fff":
                start -= 1
            # Bundle-provided entity fields cover full names.  The short tail
            # catches an injected replacement such as ``乙公司`` without
            # treating surrounding prose as part of the entity.
            if index > start:
                found.add(normalize_fact(text[index - 1:index + len(suffix)]))
            offset = index + len(suffix)
    found.update(normalize_fact(match.group()) for match in re.finditer(r"产品[A-Za-z0-9]{1,16}", text))
    return found


def normalize_fact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold().replace(",", "")


def extract_hard_facts(text: str, *, entities: Iterable[str] = ()) -> frozenset[str]:
    """Return normalized hard-fact tokens visible in ``text``.

    Explicit entity values supplied by a frozen bundle are included only when
    they literally occur in the text, avoiding an external-knowledge path.
    """
    found: set[str] = set()
    for pattern in (_NUMBER, _CHINESE_NUMBER, _PERCENT, _CURRENCY, _DATE, _TICKER, _COMPARISON, _DIRECTION):
        found.update(normalize_fact(match.group()) for match in pattern.finditer(text))
    found.update(_implicit_entities(text))
    for entity in entities:
        entity = str(entity).strip()
        if entity and entity in text:
            found.add(normalize_fact(entity))
    return frozenset(found)


def mapping_entities(value: object) -> tuple[str, ...]:
    """Read only explicit, local entity fields from a content record."""
    if not isinstance(value, Mapping):
        return ()
    entities: list[str] = []
    for key in ("entity", "entities", "company", "company_name", "product", "product_name", "issuer", "ticker", "symbol"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            entities.append(candidate)
        elif isinstance(candidate, (list, tuple)):
            entities.extend(str(item) for item in candidate if isinstance(item, str))
    return tuple(entities)

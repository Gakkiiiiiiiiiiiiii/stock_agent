"""Shared helpers/enums for app.routers (从原 app/api.py 平移，行为不变)。"""
from __future__ import annotations

import json

from fastapi import HTTPException

from engines.content.knowledge_enums import (
    KnowledgeKind,
    LifecycleStatus,
    TemporalClass,
    VerificationStatus,
)

MAX_API_LIST_LIMIT = 200
# 枚举统一（P0-11 / 设计文档 §36）：与 MCP / Lifecycle 共用 knowledge_enums 单一事实来源。
VALID_KNOWLEDGE_KINDS = KnowledgeKind.values()
VALID_TEMPORAL_CLASSES = TemporalClass.values()
VALID_LIFECYCLE_STATUSES = LifecycleStatus.values()
VALID_VERIFICATION_STATUSES = VerificationStatus.values()


def _safe_api_limit(value: int | None, *, default: int, maximum: int = MAX_API_LIST_LIMIT) -> tuple[int, list[str]]:
    warnings: list[str] = []
    try:
        limit = int(value if value is not None else default)
    except (TypeError, ValueError):
        limit = default
        warnings.append("invalid_limit_defaulted")
    if limit <= 0:
        limit = default
        warnings.append("non_positive_limit_defaulted")
    if limit > maximum:
        limit = maximum
        warnings.append(f"limit_clamped_to_{maximum}")
    return limit, warnings


def _normalize_enum_param(value: str | None, allowed: set[str], field: str) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).upper()
    if normalized not in allowed:
        raise HTTPException(status_code=400, detail=f"invalid {field}: {value}")
    return normalized


def _validate_knowledge_filters(filters: dict) -> dict:
    validated = dict(filters or {})
    enum_fields = {
        "knowledge_kind": VALID_KNOWLEDGE_KINDS,
        "temporal_class": VALID_TEMPORAL_CLASSES,
        "lifecycle_status": VALID_LIFECYCLE_STATUSES,
        "verification_status": VALID_VERIFICATION_STATUSES,
    }
    for field, allowed in enum_fields.items():
        value = validated.get(field)
        if isinstance(value, list):
            validated[field] = [_normalize_enum_param(item, allowed, field) for item in value if item not in (None, "")]
        else:
            normalized = _normalize_enum_param(value, allowed, field)
            if normalized is None:
                validated.pop(field, None)
            else:
                validated[field] = normalized
    return validated


def _format_sse(event: str, payload: dict) -> bytes:
    message = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    return message.encode("utf-8")


def _parse_job_result(value) -> dict | None:
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"result_ref": value}

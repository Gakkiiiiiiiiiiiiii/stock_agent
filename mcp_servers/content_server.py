from __future__ import annotations

from engines.content.knowledge_enums import (
    KnowledgeKind,
    LifecycleStatus,
    TemporalClass,
    VerificationStatus,
)
from engines.content.video_ingest_service import VideoIngestService


service = VideoIngestService()
MAX_TOP_K = 100
# 枚举统一（P0-11 / 设计文档 §36）：与 API / Lifecycle 共用 knowledge_enums 单一事实来源。
VALID_KNOWLEDGE_KINDS = KnowledgeKind.values()
VALID_TEMPORAL_CLASSES = TemporalClass.values()
VALID_LIFECYCLE_STATUSES = LifecycleStatus.values()
VALID_VERIFICATION_STATUSES = VerificationStatus.values()


def ingest_bilibili_video(
    url: str | None = None,
    bv_id: str | None = None,
    force_reprocess: bool = False,
    summary_mode: str = "investment",
    index_to_memory: bool = True,
    use_diarization: bool = False,
    language_hint: str | None = "zh",
) -> dict:
    return service.enqueue_bilibili(
        url=url,
        bv_id=bv_id,
        force_reprocess=force_reprocess,
        summary_mode=summary_mode,
        index_to_memory=index_to_memory,
        use_diarization=use_diarization,
        language_hint=language_hint,
    )


def ingest_xiaoe_hls_video(
    m3u8_url: str,
    page_url: str | None = None,
    title: str | None = None,
    platform_video_id: str | None = None,
    headers: dict[str, str] | None = None,
    authorized_content: bool = False,
    force_reprocess: bool = False,
    summary_mode: str = "investment",
    index_to_memory: bool = True,
    use_diarization: bool = False,
    language_hint: str | None = "zh",
    enable_visual_context: bool = False,
    engine: str = "ffmpeg-direct",
    quality: str = "best",
    workers: int = 4,
    timeout_seconds: int = 30,
) -> dict:
    return service.enqueue_xiaoe_hls(
        m3u8_url=m3u8_url,
        page_url=page_url,
        title=title,
        platform_video_id=platform_video_id,
        headers=headers,
        authorized_content=authorized_content,
        force_reprocess=force_reprocess,
        summary_mode=summary_mode,
        index_to_memory=index_to_memory,
        use_diarization=use_diarization,
        language_hint=language_hint,
        enable_visual_context=enable_visual_context,
        engine=engine,
        quality=quality,
        workers=workers,
        timeout_seconds=timeout_seconds,
    )


def get_video_summary(video_id: int, summary_mode: str = "investment") -> dict:
    detail = service.get_video_detail(video_id, summary_mode=summary_mode)
    if detail is None:
        return {"found": False, "video_id": video_id}
    return {"found": True, **detail}


def get_video_transcript_segments(video_id: int) -> dict:
    payload = service.get_video_segments(video_id)
    if payload is None:
        return {"found": False, "video_id": video_id, "segments": []}
    return {"found": True, **payload}


def search_video_insights(query: str, top_k: int = 5, themes: list[str] | None = None) -> dict:
    if not str(query or "").strip():
        return {"error": {"code": "EMPTY_QUERY", "message": "query is required"}, "deprecated": True, "replacement": "search_video_knowledge"}
    top_k, warnings = _safe_top_k(top_k)
    filters = {}
    if themes:
        filters["subject_key"] = themes
    payload = service.search_video_knowledge(query=query, filters=filters, limit=top_k)
    return {"deprecated": True, "replacement": "search_video_knowledge", **payload, "warnings": [*(payload.get("warnings") or []), *warnings]}


def search_video_knowledge(
    query: str,
    intent: str | None = None,
    filters: dict | None = None,
    top_k: int = 5,
    primary_domain: str | None = None,
    knowledge_kind: str | None = None,
    temporal_class: str | None = None,
    lifecycle_status: str | None = None,
    verification_status: str | None = None,
    subject_key: str | None = None,
    predicate_key: str | None = None,
    valid_only: bool = True,
) -> dict:
    """搜索视频知识（带统一质量门，P0-10 / 设计文档 §32-34）。

    intent 语义：消费场景标识（research / factual_qa / current_state /
    trading_decision / author_viewpoint 等），默认按 "research" 处理。
    intent → KnowledgeAccessPolicy 质量门（最低 support_status、support score、
    truth_status、review gate、valid_only），policy 合并在 service 层统一做一次；
    调用方 filters 只能收紧 policy，不能放宽（§28 strictest merge）。
    本层不做 merge，只透传 intent + 原始 filters，避免双重 merge 冲突。

    返回 item 中的 source_reliability_score 语义为“来源（作者/平台）可靠性”，
    不是单条证据的置信度（P1-5 / §56-57），不能替代 Evidence Verification。
    """
    if not str(query or "").strip():
        return {"error": {"code": "EMPTY_QUERY", "message": "query is required"}, "items": [], "limit": 0, "warnings": ["empty_query"]}
    top_k, warnings = _safe_top_k(top_k)
    merged_filters = dict(filters or {})
    merged_filters.update(
        {
        key: value
        for key, value in {
            "primary_domain": primary_domain,
            "knowledge_kind": knowledge_kind,
            "temporal_class": temporal_class,
            "lifecycle_status": lifecycle_status,
            "verification_status": verification_status,
            "subject_key": subject_key,
            "predicate_key": predicate_key,
            "valid_only": valid_only,
        }.items()
        if value not in (None, "", False)
        }
    )
    validation_error = _validate_filters_in_place(merged_filters)
    if validation_error:
        return {"error": validation_error, "items": [], "limit": top_k, "filters": merged_filters, "warnings": warnings}
    payload = service.search_video_knowledge(query=query, filters=merged_filters, limit=top_k, intent=intent)
    return {"intent": intent or "research", **payload, "warnings": [*(payload.get("warnings") or []), *warnings]}


def get_current_subject_state(subject_key: str, domains: list[str] | None = None, domain: str | None = None, top_k: int = 10) -> dict:
    """主体当前状态。repository 层已内置 current-state 质量门（P1-8 / §62-63）：
    仅 ACTIVE/VALIDATED + SOURCE_SUPPORTED 及以上，且排除人工 review REJECTED。"""
    if not str(subject_key or "").strip():
        return {"error": {"code": "EMPTY_SUBJECT", "message": "subject_key is required"}, "items": [], "warnings": ["empty_subject"]}
    top_k, warnings = _safe_top_k(top_k)
    selected_domains = domains or ([domain] if domain else [None])
    results = [service.get_current_subject_state(subject_key=subject_key, domain=item, limit=top_k) for item in selected_domains]
    items = [unit for result in results for unit in result.get("items", [])]
    return {"subject_key": subject_key, "domains": [item for item in selected_domains if item], "items": items[:top_k], "limit": top_k, "next_cursor": None, "warnings": warnings}


def get_subject_history(
    subject_key: str,
    date_from: str | None = None,
    date_to: str | None = None,
    include_expired: bool = True,
    domain: str | None = None,
    top_k: int = 50,
) -> dict:
    if not str(subject_key or "").strip():
        return {"error": {"code": "EMPTY_SUBJECT", "message": "subject_key is required"}, "items": [], "warnings": ["empty_subject"]}
    top_k, warnings = _safe_top_k(top_k)
    payload = service.get_subject_history(subject_key=subject_key, domain=domain, limit=top_k)
    items = [
        unit
        for unit in payload.get("items", [])
        if (include_expired or unit.get("lifecycle_status") != "EXPIRED")
        and _within_date_range(unit.get("as_of_time") or unit.get("valid_from"), date_from, date_to)
    ]
    return payload | {"date_from": date_from, "date_to": date_to, "include_expired": include_expired, "items": items[:top_k], "limit": top_k, "warnings": [*(payload.get("warnings") or []), *warnings]}


def get_video_knowledge_units(video_id: int, filters: dict | None = None, top_k: int = 100) -> dict:
    top_k, warnings = _safe_top_k(top_k)
    filters = dict(filters or {})
    validation_error = _validate_filters_in_place(filters)
    if validation_error:
        return {"found": False, "video_id": video_id, "error": validation_error, "items": [], "limit": top_k, "filters": filters, "warnings": warnings}
    payload = service.list_video_knowledge_units(video_id, filters=filters, limit=top_k)
    if payload is None:
        return {"found": False, "video_id": video_id, "items": [], "warnings": warnings}
    return {"found": True, **payload, "warnings": [*(payload.get("warnings") or []), *warnings]}


def get_knowledge_unit(unit_id: int) -> dict:
    payload = service.get_knowledge_unit(unit_id)
    if payload is None:
        return {"found": False, "unit_id": unit_id}
    return {"found": True, **payload}


def list_knowledge_conflicts(subject_key: str | None = None, status: str | None = None, top_k: int = 50) -> dict:
    top_k, warnings = _safe_top_k(top_k)
    status = _normalize_enum_value(status, VALID_LIFECYCLE_STATUSES)
    if status == "__INVALID__":
        return {"error": {"code": "INVALID_FILTER", "message": "invalid lifecycle_status/status"}, "items": [], "limit": top_k, "warnings": warnings}
    payload = service.list_knowledge_conflicts(subject_key=subject_key, limit=top_k)
    if status:
        items = []
        for group in payload.get("items", []):
            units = [unit for unit in group.get("units", []) if unit.get("lifecycle_status") == status]
            if units:
                items.append(group | {"units": units})
        payload = payload | {"items": items}
    return payload | {"status": status, "warnings": [*(payload.get("warnings") or []), *warnings]}


def _validate_filters_in_place(filters: dict) -> dict | None:
    fields = {
        "knowledge_kind": VALID_KNOWLEDGE_KINDS,
        "temporal_class": VALID_TEMPORAL_CLASSES,
        "lifecycle_status": VALID_LIFECYCLE_STATUSES,
        "verification_status": VALID_VERIFICATION_STATUSES,
    }
    for field, allowed in fields.items():
        value = filters.get(field)
        if value in (None, ""):
            filters.pop(field, None)
            continue
        if isinstance(value, list):
            normalized_values = []
            for item in value:
                normalized = _normalize_enum_value(item, allowed)
                if normalized == "__INVALID__":
                    return {"code": "INVALID_FILTER", "message": f"invalid {field}: {item}"}
                if normalized:
                    normalized_values.append(normalized)
            filters[field] = normalized_values
            continue
        normalized = _normalize_enum_value(value, allowed)
        if normalized == "__INVALID__":
            return {"code": "INVALID_FILTER", "message": f"invalid {field}: {value}"}
        if normalized:
            filters[field] = normalized
    return None


def _normalize_enum_value(value: str | None, allowed: set[str]) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).upper()
    return normalized if normalized in allowed else "__INVALID__"


def _safe_top_k(value: int | None, default: int = 10) -> tuple[int, list[str]]:
    warnings = []
    try:
        top_k = int(value if value is not None else default)
    except (TypeError, ValueError):
        top_k = default
        warnings.append("invalid_top_k_defaulted")
    if top_k <= 0:
        top_k = default
        warnings.append("non_positive_top_k_defaulted")
    if top_k > MAX_TOP_K:
        top_k = MAX_TOP_K
        warnings.append(f"top_k_clamped_to_{MAX_TOP_K}")
    return top_k, warnings


def _within_date_range(value: str | None, date_from: str | None, date_to: str | None) -> bool:
    if not value:
        return True
    date_value = value[:10]
    if date_from and date_value < date_from:
        return False
    if date_to and date_value > date_to:
        return False
    return True

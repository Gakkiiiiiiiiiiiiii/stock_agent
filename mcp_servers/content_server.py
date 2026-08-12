from __future__ import annotations

from services.subsystems import get_content_client

service = get_content_client()
MAX_TOP_K = 100
VALID_KNOWLEDGE_KINDS = {"CLAIM", "CATALYST", "RISK", "VALUATION", "EARNINGS", "FACT", "OPINION"}
VALID_TEMPORAL_CLASSES = {"POINT_IN_TIME", "INTERVAL", "TIMELESS"}
VALID_LIFECYCLE_STATUSES = {"ACTIVE", "VALIDATED", "EXPIRED", "SUPERSEDED", "REJECTED"}
VALID_VERIFICATION_STATUSES = {"PENDING", "VERIFIED", "PARTIAL", "REJECTED", "UNVERIFIED"}


def ingest_bilibili_video(url=None, bv_id=None, force_reprocess=False, summary_mode="investment", index_to_memory=True, use_diarization=False, language_hint="zh") -> dict:
    return service.enqueue_bilibili(url=url, bv_id=bv_id, force_reprocess=force_reprocess, summary_mode=summary_mode, index_to_memory=index_to_memory, use_diarization=use_diarization, language_hint=language_hint)


def ingest_xiaoe_hls_video(m3u8_url, page_url=None, title=None, platform_video_id=None, headers=None, authorized_content=False, force_reprocess=False, summary_mode="investment", index_to_memory=True, use_diarization=False, language_hint="zh", enable_visual_context=False, engine="ffmpeg-direct", quality="best", workers=4, timeout_seconds=30) -> dict:
    return service.enqueue_xiaoe_hls(m3u8_url=m3u8_url, page_url=page_url, title=title, platform_video_id=platform_video_id, headers=headers, authorized_content=authorized_content, force_reprocess=force_reprocess, summary_mode=summary_mode, index_to_memory=index_to_memory, use_diarization=use_diarization, language_hint=language_hint, enable_visual_context=enable_visual_context, engine=engine, quality=quality, workers=workers, timeout_seconds=timeout_seconds)


def get_video_summary(video_id: str, summary_mode: str = "investment") -> dict:
    detail = service.get_video_summary(video_id)
    return {"found": False, "video_id": video_id} if detail is None else {"found": True, **detail}


def get_video_transcript_segments(video_id: str) -> dict:
    payload = service.get_video_segments(video_id)
    return {"found": False, "video_id": video_id, "segments": []} if payload is None else {"found": True, **payload}


def search_video_insights(query: str, top_k: int = 5, themes: list[str] | None = None) -> dict:
    if not str(query or "").strip():
        return {"error": {"code": "EMPTY_QUERY", "message": "query is required"}, "deprecated": True, "replacement": "search_video_knowledge"}
    limit, warnings = _safe_top_k(top_k)
    payload = service.search_video_knowledge(query, filters={"subject": themes} if themes else {}, limit=limit)
    return {"deprecated": True, "replacement": "search_video_knowledge", **payload, "warnings": warnings}


def search_video_knowledge(query: str, intent=None, filters=None, top_k=5, primary_domain=None, knowledge_kind=None, temporal_class=None, lifecycle_status=None, verification_status=None, subject_key=None, predicate_key=None, valid_only=True) -> dict:
    if not str(query or "").strip():
        return {"error": {"code": "EMPTY_QUERY", "message": "query is required"}, "items": [], "limit": 0, "warnings": ["empty_query"]}
    limit, warnings = _safe_top_k(top_k)
    merged = dict(filters or {})
    aliases = {"kind": knowledge_kind, "subject": subject_key, "lifecycle_status": lifecycle_status, "review_status": verification_status, "primary_domain": primary_domain, "temporal_class": temporal_class, "predicate_key": predicate_key}
    merged.update({key: value for key, value in aliases.items() if value not in (None, "")})
    validation_error = _validate_filters_in_place(merged)
    if validation_error:
        return {"error": validation_error, "items": [], "limit": limit, "filters": merged, "warnings": warnings}
    payload = service.search_video_knowledge(query, filters=merged, limit=limit, intent=intent)
    return {"intent": intent or "research", **payload, "warnings": [*(payload.get("warnings") or []), *warnings]}


def get_current_subject_state(subject_key: str, domains=None, domain=None, top_k=10) -> dict:
    return search_video_knowledge(subject_key, intent="current_state", top_k=top_k, subject_key=subject_key)


def get_subject_history(subject_key: str, date_from=None, date_to=None, include_expired=True, domain=None, top_k=50) -> dict:
    payload = search_video_knowledge(subject_key, intent="history", top_k=top_k, subject_key=subject_key)
    items = [item for item in payload.get("items", []) if _within_date_range(item.get("as_of") or item.get("available_from"), date_from, date_to)]
    return {**payload, "date_from": date_from, "date_to": date_to, "include_expired": include_expired, "items": items}


def get_video_knowledge_units(video_id: str, filters=None, top_k=100) -> dict:
    return service.list_video_knowledge_units(video_id, filters=filters or {}, limit=top_k)


def get_knowledge_unit(unit_id: str) -> dict:
    payload = service.get_knowledge_unit(unit_id)
    return {"found": payload is not None, **(payload or {"unit_id": unit_id})}


def list_knowledge_conflicts(subject_key=None, status=None, top_k=50) -> dict:
    return {"items": [], "limit": min(top_k, MAX_TOP_K), "subject_key": subject_key, "status": status, "warning": "conflict groups are not part of content.v1"}


def _validate_filters_in_place(filters: dict) -> dict | None:
    fields = {"kind": VALID_KNOWLEDGE_KINDS, "temporal_class": VALID_TEMPORAL_CLASSES, "lifecycle_status": VALID_LIFECYCLE_STATUSES, "review_status": VALID_VERIFICATION_STATUSES}
    for field, allowed in fields.items():
        value = filters.get(field)
        if value and str(value).upper() not in allowed:
            return {"code": "INVALID_FILTER", "message": f"invalid {field}: {value}"}
        if value:
            filters[field] = str(value).upper()
    return None


def _safe_top_k(value, default=10):
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default, ["invalid_top_k_defaulted"]
    if limit <= 0:
        return default, ["non_positive_top_k_defaulted"]
    return min(limit, MAX_TOP_K), ([f"top_k_clamped_to_{MAX_TOP_K}"] if limit > MAX_TOP_K else [])


def _within_date_range(value, date_from, date_to):
    if not value:
        return True
    current = str(value)[:10]
    return not ((date_from and current < date_from) or (date_to and current > date_to))

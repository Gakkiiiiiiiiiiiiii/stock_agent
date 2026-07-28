from __future__ import annotations

from engines.content.video_ingest_service import VideoIngestService
from engines.memory.memory_retriever import retrieve_memory


service = VideoIngestService()


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
    filters = {"source_type": "bilibili_video_summary"}
    if themes:
        filters["related_theme"] = themes
    return retrieve_memory(query=query, filters=filters, top_k=top_k)

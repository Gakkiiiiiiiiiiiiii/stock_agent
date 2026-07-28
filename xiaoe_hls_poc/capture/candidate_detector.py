"""M3U8 候选识别(8.4 / 10.5):URL、Content-Type、响应体首行三重信号。"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_SNIFF_BYTES = 4096  # 10.4:响应体最多读取几 KiB


@dataclass
class RawCandidate:
    url: str
    status: int
    content_type: str
    method: str = "GET"
    page_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    has_authorization: bool = False
    body_extm3u: bool = False
    body_is_error_page: bool = False
    total_duration: float | None = None
    has_stream_inf: bool = False
    has_extinf: bool = False


def looks_like_m3u8_url(url: str) -> bool:
    return ".m3u8" in url.lower()


def looks_like_m3u8_content_type(content_type: str) -> bool:
    ct = (content_type or "").lower()
    return "mpegurl" in ct or "m3u8" in ct


def looks_like_m3u8_body(body_head: bytes | str) -> bool:
    if isinstance(body_head, bytes):
        body_head = body_head.decode("utf-8", "replace")
    return body_head.lstrip().startswith("#EXTM3U")


def is_m3u8_candidate(url: str, content_type: str = "", body_head: bytes | str = b"") -> bool:
    return (
        looks_like_m3u8_url(url)
        or looks_like_m3u8_content_type(content_type)
        or (bool(body_head) and looks_like_m3u8_body(body_head))
    )


def assess_response(response, *, page_url: str = "") -> RawCandidate | None:
    """从 Playwright Response 评估是否为 M3U8 候选。

    快速失败优先:先 URL/Content-Type,再限量读响应体确认首行。
    """
    url = response.url
    content_type = (response.headers.get("content-type") or "").lower()
    if not (looks_like_m3u8_url(url) or looks_like_m3u8_content_type(content_type)):
        return None

    body_head = b""
    try:
        body = response.body()
        body_head = body[:MAX_SNIFF_BYTES]
    except Exception:  # noqa: BLE001 - 响应体不可读时仅用 URL/CT 信号
        body_head = b""

    text_head = body_head.decode("utf-8", "replace")
    cand = RawCandidate(
        url=url,
        status=response.status,
        content_type=content_type,
        method=response.request.method,
        page_url=page_url,
        body_extm3u=looks_like_m3u8_body(text_head),
        body_is_error_page=text_head.lstrip().lower().startswith(("<!doctype", "<html", "{")),
        has_stream_inf="#EXT-X-STREAM-INF" in text_head,
        has_extinf="#EXTINF" in text_head,
    )
    try:
        headers = response.request.all_headers()
        cand.has_authorization = any(k.lower() == "authorization" for k in headers)
        from ..http.auth_header_policy import filter_reusable_headers

        cand.headers = filter_reusable_headers(headers)
    except Exception:  # noqa: BLE001
        pass
    return cand

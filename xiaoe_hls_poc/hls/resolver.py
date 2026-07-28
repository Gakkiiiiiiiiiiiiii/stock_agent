"""Playlist Resolver:递归解析 Master/Media,循环检测与深度限制(11.2)。

resolve_playlist(client, url, quality=...) 直接完成 HTTP 获取与解析,
对 401/403 与 HTML/JSON 错误页做分类报错。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import MAX_PLAYLIST_SIZE
from ..errors import ErrorCode, PocError
from ..http.response_validator import ensure_not_error_page, raise_for_classified_status
from ..models import VariantInfo
from .parser import MasterPlaylist, MediaPlaylist, detect_playlist_type, parse_playlist
from .variant_selector import select_variant

MAX_DEPTH = 4


@dataclass
class ResolvedPlaylist:
    media: MediaPlaylist
    variant: VariantInfo | None = None
    playlist_type: str = "media"  # "master" / "media"
    master: MasterPlaylist | None = None


def fetch_playlist_text(client: httpx.Client, url: str) -> str:
    """获取并校验 Playlist 文本(大小上限 / 错误页 / UTF-8 / EXTM3U)。"""
    resp = client.get(url)
    raise_for_classified_status(resp, kind="PLAYLIST")
    data = resp.content
    if len(data) > MAX_PLAYLIST_SIZE:
        raise PocError(ErrorCode.PLAYLIST_INVALID, "Playlist 超过 5MiB 上限")
    ensure_not_error_page(data, kind="PLAYLIST")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PocError(ErrorCode.PLAYLIST_INVALID, "Playlist 不是合法 UTF-8") from exc
    if detect_playlist_type(text) == "invalid":
        raise PocError(ErrorCode.PLAYLIST_INVALID, "响应不是合法 M3U8")
    return text


def resolve_playlist(
    client: httpx.Client,
    url: str,
    *,
    quality: str = "best",
    _depth: int = 0,
    _seen: frozenset[str] = frozenset(),
) -> ResolvedPlaylist:
    if _depth > MAX_DEPTH:
        raise PocError(ErrorCode.PLAYLIST_INVALID, "Playlist 递归深度超限")
    if url in _seen:
        raise PocError(ErrorCode.PLAYLIST_INVALID, "检测到 Playlist 循环引用")
    seen = _seen | {url}

    text = fetch_playlist_text(client, url)
    parsed = parse_playlist(text, url)
    if isinstance(parsed, MediaPlaylist):
        return ResolvedPlaylist(media=parsed, playlist_type="media")

    variant = select_variant(parsed.variants, quality)
    child = resolve_playlist(
        client, variant.uri, quality=quality, _depth=_depth + 1, _seen=seen
    )
    return ResolvedPlaylist(
        media=child.media,
        variant=variant,
        playlist_type="master",
        master=parsed,
    )

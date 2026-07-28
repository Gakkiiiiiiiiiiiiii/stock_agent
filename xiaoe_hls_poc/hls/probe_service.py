"""Probe 服务(第 11 节):最小网络验证,输出分类诊断。"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import httpx

from ..config import MAX_PLAYLIST_SIZE
from ..crypto.aes128 import decrypt_aes128_cbc
from ..crypto.key_manager import KeyManager
from ..errors import ErrorCode, PocError
from ..http.response_validator import (
    ensure_not_error_page,
    raise_for_classified_status,
)
from ..media.segment_validator import probe_media
from ..security.redactor import redact_url
from .iv_strategy import resolve_iv
from .parser import MediaPlaylist, detect_playlist_type, parse_playlist
from .resolver import ResolvedPlaylist, resolve_playlist


def fetch_playlist_text(client: httpx.Client, url: str) -> tuple[int, str]:
    resp = client.get(url)
    status = resp.status_code
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
    return status, text


def run_probe(
    client: httpx.Client,
    url: str,
    *,
    quality: str = "best",
    iv_strategy: str = "hls-spec",
    ffprobe_check: bool = False,
) -> dict:
    """执行 11.x 的最小验证链。返回可序列化诊断 dict(已脱敏)。"""
    report: dict = {
        "url": redact_url(url),
        "playlist_status": None,
        "playlist_type": None,
        "variant": None,
        "segment_count": 0,
        "estimated_duration": 0.0,
        "encryption_method": "NONE",
        "media_sequence_base": 0,
        "has_map": False,
        "first_key_ok": None,
        "first_segment_media_type": None,
        "iv_strategy": iv_strategy,
        "warnings": [],
        "timings": {},
    }

    t0 = time.monotonic()
    status, text = fetch_playlist_text(client, url)
    report["playlist_status"] = status
    report["timings"]["playlist_seconds"] = round(time.monotonic() - t0, 3)

    # 已取回文本时直接解析;Master 交给 resolver(按新 API 内部重新获取子播放列表)
    parsed = parse_playlist(text, url)
    if isinstance(parsed, MediaPlaylist):
        resolved = ResolvedPlaylist(media=parsed, playlist_type="media")
    else:
        resolved = resolve_playlist(client, url, quality=quality)
    media = resolved.media
    report["playlist_type"] = resolved.playlist_type
    if resolved.variant:
        report["variant"] = resolved.variant.model_dump(exclude={"uri"})
    report["segment_count"] = len(media.segments)
    report["estimated_duration"] = round(media.total_duration, 3)
    report["encryption_method"] = media.encryption_method
    report["media_sequence_base"] = media.media_sequence_base
    report["has_map"] = media.map_uri is not None

    first = media.segments[0]
    key_manager = KeyManager(lambda u: _fetch_bytes(client, u, kind="KEY"))
    plaintext: bytes
    if first.key_context and first.key_context.method.upper() == "AES-128":
        t1 = time.monotonic()
        key = key_manager.resolve(first.key_context)
        report["first_key_ok"] = True
        report["timings"]["key_seconds"] = round(time.monotonic() - t1, 3)
        ciphertext = _fetch_bytes(client, first.uri_secret, kind="SEGMENT")
        iv = resolve_iv(
            iv_strategy, first.key_context.explicit_iv, first.media_sequence, first.index
        )
        plaintext = decrypt_aes128_cbc(key, iv, ciphertext)
    else:
        plaintext = _fetch_bytes(client, first.uri_secret, kind="SEGMENT")

    media_type = probe_media(plaintext)
    report["first_segment_media_type"] = media_type
    if media_type is None:
        raise PocError(
            ErrorCode.DECRYPT_MEDIA_INVALID,
            "首分片解密后未通过 TS/fMP4 探测(检查 Key/IV/Media Sequence)",
        )

    if ffprobe_check:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(plaintext)
            tmp_path = Path(tmp.name)
        try:
            from ..media.ffprobe import probe_file

            pr = probe_file(tmp_path)
            report["ffprobe_stream_count"] = pr.stream_count
            if pr.stream_count == 0:
                report["warnings"].append("ffprobe 未识别出媒体流")
        finally:
            tmp_path.unlink(missing_ok=True)

    return report


def _fetch_bytes(client: httpx.Client, url: str, *, kind: str) -> bytes:
    resp = client.get(url)
    raise_for_classified_status(resp, kind=kind)
    return resp.content

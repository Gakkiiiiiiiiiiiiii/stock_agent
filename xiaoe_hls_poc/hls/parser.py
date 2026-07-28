"""HLS Playlist 状态机解析(设计文档第 12 节)。

逐行解析 Media Playlist,维护 key/map/discontinuity/duration 状态,
遇到 URI 行时将当前状态快照绑定到 SegmentTask。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

from ..errors import ErrorCode, PocError
from ..models import KeyContext, SegmentTask, VariantInfo
from ..security.authorization_guard import assert_encryption_supported
from ..security.redactor import safe_query_fingerprint


@dataclass
class MasterPlaylist:
    variants: list[VariantInfo] = field(default_factory=list)
    base_url: str = ""


@dataclass
class MediaPlaylist:
    segments: list[SegmentTask] = field(default_factory=list)
    media_sequence_base: int = 0
    target_duration: float = 0.0
    has_endlist: bool = False
    total_duration: float = 0.0
    map_uri: str | None = None
    map_key_context: KeyContext | None = None
    has_discontinuity: bool = False
    encryption_method: str = "NONE"
    base_url: str = ""


def detect_playlist_type(text: str) -> str:
    """master / media / invalid(11.2)。"""
    if not text.lstrip().startswith("#EXTM3U"):
        return "invalid"
    if "#EXT-X-STREAM-INF" in text:
        return "master"
    if "#EXTINF" in text:
        return "media"
    return "invalid"


def _parse_attribute_list(value: str) -> dict[str, str]:
    """解析 key=value 属性表(带引号值内的逗号不算分隔)。"""
    attrs: dict[str, str] = {}
    token = ""
    parts: list[str] = []
    in_quote = False
    for ch in value:
        if ch == '"':
            in_quote = not in_quote
            token += ch
        elif ch == "," and not in_quote:
            parts.append(token)
            token = ""
        else:
            token += ch
    if token:
        parts.append(token)
    for part in parts:
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        attrs[k.strip().upper()] = v
    return attrs


def _parse_resolution(value: str) -> tuple[int | None, int | None]:
    try:
        w, _, h = value.partition("x")
        return int(w), int(h)
    except ValueError:
        return None, None


def parse_master(text: str, base_url: str = "") -> MasterPlaylist:
    if not text.lstrip().startswith("#EXTM3U"):
        raise PocError(ErrorCode.PLAYLIST_INVALID, "不是合法的 M3U8(缺少 #EXTM3U)")
    variants: list[VariantInfo] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    pending: dict[str, str] | None = None
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = _parse_attribute_list(line.split(":", 1)[1])
        elif pending is not None and not line.startswith("#"):
            width, height = _parse_resolution(pending.get("RESOLUTION", ""))
            variants.append(
                VariantInfo(
                    uri=urljoin(base_url, line),
                    bandwidth=_int_or_none(pending.get("BANDWIDTH")),
                    average_bandwidth=_int_or_none(pending.get("AVERAGE-BANDWIDTH")),
                    width=width,
                    height=height,
                    codecs=pending.get("CODECS"),
                    audio_group=pending.get("AUDIO"),
                )
            )
            pending = None
    if not variants:
        raise PocError(ErrorCode.MASTER_NO_VARIANT, "Master Playlist 无可用 Variant")
    return MasterPlaylist(variants=variants, base_url=base_url)


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _build_key_context(attrs: dict[str, str], base_url: str) -> KeyContext:
    method = attrs.get("METHOD", "NONE").upper()
    key_format = attrs.get("KEYFORMAT", "identity")
    assert_encryption_supported(method, key_format)
    uri = attrs.get("URI")
    iv = attrs.get("IV")
    if iv and iv.lower().startswith("0x"):
        iv = iv[2:]
    if iv is not None:
        try:
            raw = bytes.fromhex(iv)
        except ValueError as exc:
            raise PocError(ErrorCode.PLAYLIST_INVALID, "EXT-X-KEY IV 不是合法十六进制") from exc
        if len(raw) != 16:
            raise PocError(ErrorCode.PLAYLIST_INVALID, "EXT-X-KEY IV 必须为 128 位")
    full_uri = urljoin(base_url, uri) if uri else None
    return KeyContext(
        method=method,
        explicit_iv_hex=iv,
        key_format=key_format,
        key_id=safe_query_fingerprint(full_uri) if full_uri else "",
        uri_secret=full_uri,
    )


def parse_media(text: str, base_url: str = "") -> MediaPlaylist:
    """状态机解析 Media Playlist(12.1)。"""
    if detect_playlist_type(text) != "media":
        raise PocError(ErrorCode.PLAYLIST_INVALID, "不是合法的 Media Playlist")

    playlist = MediaPlaylist(base_url=base_url)
    current_key: KeyContext | None = None
    current_map_uri: str | None = None
    current_map_key: KeyContext | None = None
    current_duration: float | None = None
    current_discontinuity = False
    segment_index = 0
    seq_base_set = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            playlist.media_sequence_base = int(line.split(":", 1)[1].strip())
            seq_base_set = True
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            playlist.target_duration = float(line.split(":", 1)[1].strip())
        elif line.startswith("#EXT-X-KEY:"):
            kc = _build_key_context(_parse_attribute_list(line.split(":", 1)[1]), base_url)
            current_key = None if kc.method == "NONE" else kc
            if kc.method != "NONE":
                playlist.encryption_method = kc.method
        elif line.startswith("#EXT-X-MAP:"):
            attrs = _parse_attribute_list(line.split(":", 1)[1])
            if "BYTERANGE" in attrs:
                raise PocError(
                    ErrorCode.BYTERANGE_UNSUPPORTED, "EXT-X-MAP 带 BYTERANGE,第一版不支持"
                )
            map_uri = attrs.get("URI")
            current_map_uri = urljoin(base_url, map_uri) if map_uri else None
            # MAP 使用其出现位置的 KeyContext(无 EXT-X-KEY 前缀时用当前 key)
            current_map_key = current_key
            playlist.map_uri = current_map_uri
            playlist.map_key_context = current_map_key
        elif line.startswith("#EXT-X-BYTERANGE"):
            raise PocError(
                ErrorCode.BYTERANGE_UNSUPPORTED,
                "检测到 EXT-X-BYTERANGE,第一版仅检测不支持",
            )
        elif line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
            # 仅记录,不影响 media sequence 计算
            pass
        elif line.startswith("#EXT-X-DISCONTINUITY"):
            current_discontinuity = True
            playlist.has_discontinuity = True
        elif line.startswith("#EXT-X-I-FRAMES-ONLY"):
            raise PocError(
                ErrorCode.METHOD_UNSUPPORTED, "I-Frame Playlist 不支持"
            )
        elif line.startswith("#EXTINF:"):
            value = line.split(":", 1)[1].split(",", 1)[0]
            current_duration = float(value)
        elif line.startswith("#EXT-X-ENDLIST"):
            playlist.has_endlist = True
        elif line.startswith("#"):
            continue
        else:
            # URI 行:快照当前状态(KeyContext 深拷贝,分片间互不影响)
            task = SegmentTask(
                index=segment_index,
                media_sequence=playlist.media_sequence_base + segment_index,
                duration=current_duration or 0.0,
                key_context=current_key.model_copy(deep=True) if current_key else None,
                map_uri_secret=current_map_uri,
                map_key_context=current_map_key.model_copy(deep=True) if current_map_key else None,
                discontinuity=current_discontinuity,
                uri_secret=urljoin(base_url, line),
            )
            playlist.segments.append(task)
            playlist.total_duration += task.duration
            segment_index += 1
            current_duration = None
            current_discontinuity = False

    _ = seq_base_set  # 缺省 base=0 已在模型默认中体现
    if not playlist.segments:
        raise PocError(ErrorCode.PLAYLIST_INVALID, "Media Playlist 无分片")
    if not playlist.has_endlist:
        raise PocError(
            ErrorCode.LIVE_PLAYLIST_UNSUPPORTED,
            "缺少 #EXT-X-ENDLIST,判定为 Live Playlist,PoC 默认拒绝",
        )
    return playlist


def parse_playlist(text: str, base_url: str = "") -> MasterPlaylist | MediaPlaylist:
    kind = detect_playlist_type(text)
    if kind == "master":
        return parse_master(text, base_url)
    if kind == "media":
        return parse_media(text, base_url)
    raise PocError(
        ErrorCode.PLAYLIST_INVALID,
        "响应不是合法 M3U8(缺少 #EXTM3U/#EXTINF/#EXT-X-STREAM-INF)",
    )

"""IV 策略(12.4)。默认严格按 HLS 规范;兼容策略需显式启用。"""

from __future__ import annotations

from ..errors import ErrorCode, PocError

HLS_SPEC = "hls-spec"
XIAOE_LEGACY_INDEX_TAIL = "xiaoe-legacy-index-tail"

SUPPORTED_STRATEGIES = (HLS_SPEC, XIAOE_LEGACY_INDEX_TAIL)


def iv_hls_spec(explicit_iv: bytes | None, media_sequence: int, index: int) -> bytes:
    """标准:显式 IV 优先,否则 Media Sequence Number 按 128 位大端补零。"""
    if explicit_iv is not None:
        if len(explicit_iv) != 16:
            raise PocError(ErrorCode.PLAYLIST_INVALID, "显式 IV 必须为 16 字节")
        return explicit_iv
    return media_sequence.to_bytes(16, "big")


def iv_legacy_index_tail(
    explicit_iv: bytes | None, media_sequence: int, index: int
) -> bytes:
    """兼容:base_iv[0:12] + 序号(4 字节大端)。仅在用户显式启用时使用。"""
    base = explicit_iv if explicit_iv is not None else bytes(16)
    if len(base) != 16:
        raise PocError(ErrorCode.PLAYLIST_INVALID, "基础 IV 必须为 16 字节")
    return base[:12] + media_sequence.to_bytes(4, "big")


def resolve_iv(
    strategy: str,
    explicit_iv: bytes | None,
    media_sequence: int,
    index: int = 0,
) -> bytes:
    if strategy == HLS_SPEC:
        return iv_hls_spec(explicit_iv, media_sequence, index)
    if strategy == XIAOE_LEGACY_INDEX_TAIL:
        return iv_legacy_index_tail(explicit_iv, media_sequence, index)
    raise PocError(
        ErrorCode.INPUT_INVALID,
        f"未知 IV 策略: {strategy}",
        hint=f"支持: {', '.join(SUPPORTED_STRATEGIES)}",
    )

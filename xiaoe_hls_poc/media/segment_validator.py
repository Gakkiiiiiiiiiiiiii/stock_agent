"""TS / fMP4 媒体签名探测(设计文档第 15 节)。"""

from __future__ import annotations

TS_SYNC_BYTE = 0x47
TS_PACKET_SIZE = 188
TS_MIN_PACKETS = 3
TS_SCAN_LIMIT = TS_PACKET_SIZE * 4


def detect_ts(data: bytes) -> bool:
    """在合理偏移内找到 0x47,且后续 188 字节周期处继续出现(15.1)。"""
    if len(data) < TS_PACKET_SIZE * TS_MIN_PACKETS:
        return False
    scan_end = min(TS_SCAN_LIMIT, len(data) - TS_PACKET_SIZE * (TS_MIN_PACKETS - 1) - 1)
    for offset in range(scan_end + 1):
        if data[offset] != TS_SYNC_BYTE:
            continue
        if all(
            data[offset + TS_PACKET_SIZE * i] == TS_SYNC_BYTE
            for i in range(1, TS_MIN_PACKETS)
            if offset + TS_PACKET_SIZE * i < len(data)
        ):
            return True
    return False


def _read_box_type(data: bytes, offset: int) -> str | None:
    if offset + 8 > len(data):
        return None
    return data[offset + 4 : offset + 8].decode("latin-1", errors="replace")


def list_top_boxes(data: bytes, max_boxes: int = 16) -> list[str]:
    boxes: list[str] = []
    offset = 0
    while offset + 8 <= len(data) and len(boxes) < max_boxes:
        size = int.from_bytes(data[offset : offset + 4], "big")
        box_type = _read_box_type(data, offset)
        if box_type is None:
            break
        boxes.append(box_type)
        if size == 1:  # largesize
            if offset + 16 > len(data):
                break
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
        if size == 0:
            break
        if size < 8:
            break
        offset += size
    return boxes


def detect_fmp4_init(data: bytes) -> bool:
    """初始化段:ftyp + moov(15.2)。"""
    boxes = list_top_boxes(data)
    return "ftyp" in boxes and "moov" in boxes


def detect_fmp4_fragment(data: bytes) -> bool:
    """媒体分片:moof + mdat。"""
    boxes = list_top_boxes(data)
    return "moof" in boxes


def probe_media(data: bytes) -> str | None:
    """返回 'ts' / 'fmp4-init' / 'fmp4' / None。"""
    if detect_ts(data):
        return "ts"
    if detect_fmp4_init(data):
        return "fmp4-init"
    if detect_fmp4_fragment(data):
        return "fmp4"
    return None


def looks_like_error_page(data: bytes) -> bool:
    """HTML/JSON 错误页判定(13.3)。"""
    head = data[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or head.startswith(b'{"')

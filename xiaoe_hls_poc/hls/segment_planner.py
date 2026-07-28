"""Segment Planner:把 Media Playlist 映射为带本地路径的下载计划。"""

from __future__ import annotations

from pathlib import Path

from ..config import MAX_SEGMENT_COUNT
from ..errors import ErrorCode, PocError
from ..models import SegmentTask
from .parser import MediaPlaylist


def build_plan(
    media: MediaPlaylist,
    job_dir: Path,
    *,
    probe_segments: int | None = None,
) -> list[SegmentTask]:
    segments = media.segments
    if len(segments) > MAX_SEGMENT_COUNT:
        raise PocError(
            ErrorCode.INPUT_INVALID,
            f"分片数 {len(segments)} 超过上限 {MAX_SEGMENT_COUNT}",
        )
    if probe_segments is not None:
        segments = segments[: max(0, probe_segments)]

    encrypted_dir = job_dir / "encrypted"
    plain_dir = job_dir / "plain"
    plan: list[SegmentTask] = []
    for task in segments:
        task.local_encrypted_path = str(encrypted_dir / f"seg-{task.index:06d}.enc")
        task.local_plain_path = str(plain_dir / f"seg-{task.index:06d}.bin")
        plan.append(task)
    return plan

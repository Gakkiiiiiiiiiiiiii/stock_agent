"""受控刷新协调器(10.9):401/403 时最多一次重新捕获,媒体一致性校验。"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import MAX_AUTH_REFRESH
from ..errors import ErrorCode, PocError
from ..hls.parser import MediaPlaylist


@dataclass
class RefreshState:
    count: int = 0

    def can_refresh(self, refresh_on_expired: str) -> bool:
        return refresh_on_expired == "once" and self.count < MAX_AUTH_REFRESH

    def note_refreshed(self) -> None:
        self.count += 1


def assert_refresh_allowed(state: RefreshState, refresh_on_expired: str) -> None:
    if not state.can_refresh(refresh_on_expired):
        raise PocError(
            ErrorCode.AUTH_REFRESH_LIMIT_REACHED,
            "已达到授权刷新次数上限",
            hint="重新打开课程页捕获新地址后再试",
        )


def assert_same_media(old: MediaPlaylist, new: MediaPlaylist) -> None:
    """刷新后必须是同一视频(10.9):类型、时长、分片数/序列一致,否则停止。"""
    if old.encryption_method != new.encryption_method:
        raise PocError(
            ErrorCode.REFRESH_MEDIA_MISMATCH,
            "刷新后加密方式变化,判定不是同一视频,停止",
        )
    if abs(old.total_duration - new.total_duration) > max(3.0, old.total_duration * 0.01):
        raise PocError(
            ErrorCode.REFRESH_MEDIA_MISMATCH,
            f"刷新后时长不一致({old.total_duration:.1f}s vs {new.total_duration:.1f}s),停止",
        )
    if len(old.segments) != len(new.segments):
        raise PocError(
            ErrorCode.REFRESH_MEDIA_MISMATCH,
            f"刷新后分片数不一致({len(old.segments)} vs {len(new.segments)}),停止",
        )
    if old.media_sequence_base != new.media_sequence_base:
        raise PocError(
            ErrorCode.REFRESH_MEDIA_MISMATCH,
            "刷新后 Media Sequence 起点不一致,停止",
        )

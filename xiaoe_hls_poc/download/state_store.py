"""断点续传状态(第 16 节)。state.json 不存明文 Key/Cookie/签名 URL。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..models import SegmentState, SegmentTask
from ..security.redactor import url_fingerprint


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StateStore:
    def __init__(self, job_dir: Path):
        self.job_dir = job_dir
        self.path = job_dir / "state.json"
        self.playlist_hash: str = ""
        self.segments: dict[int, SegmentState] = {}

    def load(self) -> bool:
        if not self.path.is_file():
            return False
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.playlist_hash = data.get("playlist_hash", "")
        self.segments = {
            int(k): SegmentState(**v) for k, v in data.get("segments", {}).items()
        }
        return True

    def save(self) -> None:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "playlist_hash": self.playlist_hash,
            "segments": {str(k): v.model_dump() for k, v in sorted(self.segments.items())},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, index: int) -> SegmentState:
        if index not in self.segments:
            self.segments[index] = SegmentState(index=index)
        return self.segments[index]

    # ---- 便捷 API(mark / can_reuse / playlist 兼容性) ----

    def _ensure_loaded(self) -> None:
        if not self.segments and not self.playlist_hash and self.path.is_file():
            self.load()

    def mark(
        self,
        task: SegmentTask,
        status: str,
        *,
        size: int = 0,
        sha256: str | None = None,
        attempts: int = 0,
        error_code: str | None = None,
    ) -> None:
        """记录分片状态并立即持久化。URI 只存脱敏哈希(16.2)。"""
        st = self.get(task.index)
        st.status = status
        st.attempts = attempts or st.attempts
        st.bytes_downloaded = size or st.bytes_downloaded
        st.sha256 = sha256 or st.sha256
        st.uri_hash = url_fingerprint(task.uri_secret)
        st.error_code = error_code
        self.save()

    def can_reuse(self, task: SegmentTask) -> bool:
        """Resume 判定(16.3):状态 + URI 哈希 + 文件存在 + 大小 + SHA-256。"""
        self._ensure_loaded()
        st = self.segments.get(task.index)
        if st is None:
            return False
        local = Path(task.local_plain_path)
        return can_reuse_segment(
            st,
            task,
            local,
            playlist_hash_current=self.playlist_hash,
            playlist_hash_saved=self.playlist_hash,
        )

    def init_playlist(self, playlist_text: str) -> None:
        self.playlist_hash = sha256_bytes(playlist_text.encode("utf-8"))
        self.save()

    def playlist_compatible(self, playlist_text: str) -> bool:
        """Playlist 哈希未发生不兼容变化(16.3)。无历史状态则不兼容。"""
        self._ensure_loaded()
        if not self.playlist_hash:
            return False
        return self.playlist_hash == sha256_bytes(playlist_text.encode("utf-8"))


def can_reuse_segment(
    state: SegmentState,
    task: SegmentTask,
    local_path: Path,
    *,
    playlist_hash_current: str,
    playlist_hash_saved: str,
) -> bool:
    """Resume 判定(16.3):URI 哈希一致 + 文件存在 + 大小一致 + SHA-256 一致
    + Playlist 哈希未发生不兼容变化。"""
    if state.status not in ("verified", "decrypted"):
        return False
    if playlist_hash_saved and playlist_hash_current != playlist_hash_saved:
        return False
    if state.uri_hash != url_fingerprint(task.uri_secret):
        return False
    if not local_path.is_file():
        return False
    size = local_path.stat().st_size
    if state.bytes_downloaded and size != state.bytes_downloaded:
        return False
    if not state.sha256:
        return False
    return sha256_file(local_path) == state.sha256

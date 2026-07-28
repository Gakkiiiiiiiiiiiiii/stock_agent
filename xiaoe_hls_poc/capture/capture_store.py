"""Capture 持久化(7.1 / 10.7):capture.json 脱敏存储,明文进 secret store。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..auth import secret_store
from ..config import captures_dir, protect_file
from ..errors import ErrorCode, PocError
from ..models import CapturedMediaRequest
from ..security.redactor import redact_url
from .candidate_detector import RawCandidate


def save_capture(
    cand: RawCandidate,
    *,
    profile_name: str,
    score: float,
    course_url: str = "",
) -> CapturedMediaRequest:
    capture_id = uuid.uuid4().hex[:12]
    # 明文 URL 与 Authorization 只进受保护 secret 文件
    secret_id = secret_store.put_secret({
        "capture_id": capture_id,
        "playlist_url": cand.url,
        "profile_name": profile_name,
    })
    capture = CapturedMediaRequest(
        capture_id=capture_id,
        auth_context_id=profile_name,
        page_url=cand.page_url or course_url,
        playlist_url_redacted=redact_url(cand.url),
        method=cand.method,
        response_status=cand.status,
        content_type=cand.content_type,
        candidate_score=score,
        has_authorization=cand.has_authorization,
        headers_secret_ref=secret_id,
    )
    d = captures_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{capture_id}.json"
    p.write_text(capture.model_dump_json(indent=2), encoding="utf-8")
    protect_file(p)
    return capture


def load_capture(capture_id: str) -> CapturedMediaRequest:
    if not capture_id or not capture_id.isalnum():
        raise PocError(ErrorCode.INPUT_INVALID, "非法 capture-id")
    p = captures_dir() / f"{capture_id}.json"
    if not p.is_file():
        raise PocError(
            ErrorCode.INPUT_INVALID, f"capture 不存在: {capture_id}",
            hint="先执行 capture 命令获取播放地址",
        )
    try:
        return CapturedMediaRequest(**json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PocError(ErrorCode.INTERNAL_ERROR, "capture.json 损坏") from exc


def resolve_capture_url(capture: CapturedMediaRequest) -> str:
    """从 secret store 取回明文 URL(仅内存使用)。"""
    secret = secret_store.get_secret(capture.headers_secret_ref)
    if not secret or not secret.get("playlist_url"):
        raise PocError(
            ErrorCode.AUTH_CONTEXT_INCOMPLETE,
            "capture 对应的明文 URL 缺失(secret 已被清理),请重新 capture",
        )
    return secret["playlist_url"]


def list_captures() -> list[CapturedMediaRequest]:
    d = captures_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(CapturedMediaRequest(**json.loads(p.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return out


def delete_capture(capture_id: str) -> None:
    if capture_id and capture_id.isalnum():
        (captures_dir() / f"{capture_id}.json").unlink(missing_ok=True)

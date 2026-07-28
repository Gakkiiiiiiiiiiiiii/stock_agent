"""受保护 Secret 存储(10.7):明文 URL/Authorization 只进 secret 文件,不进 capture.json。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..config import _chmod_user_only, home_dir, protect_file


def secrets_dir() -> Path:
    d = home_dir() / "secrets"
    d.mkdir(parents=True, exist_ok=True)
    _chmod_user_only(d)
    return d


def put_secret(payload: dict) -> str:
    """写入一个 secret 文件,返回 secret 引用 ID。"""
    secret_id = uuid.uuid4().hex[:16]
    p = secrets_dir() / f"{secret_id}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    protect_file(p)
    return secret_id


def get_secret(secret_id: str) -> dict | None:
    if not secret_id or not secret_id.isalnum():
        return None
    p = secrets_dir() / f"{secret_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def delete_secret(secret_id: str) -> None:
    if secret_id and secret_id.isalnum():
        (secrets_dir() / f"{secret_id}.json").unlink(missing_ok=True)

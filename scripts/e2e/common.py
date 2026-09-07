"""Small safety primitives shared by the API-only E2E tools."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SENSITIVE_KEY = re.compile(r"(?:authorization|cookie|credential|secret|token|storage|header|model[_-]?response|private|signed)", re.IGNORECASE)


class E2EError(RuntimeError):
    """A stable, intentionally body-free error reported by the driver."""

    def __init__(self, code: str, stage: str) -> None:
        super().__init__(code)
        self.code, self.stage = code, stage


def fixed_base_url(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError(f"{label}_BASE_URL_INVALID")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def read_secret_file(value: str | Path, *, code: str) -> str:
    path = Path(value)
    try:
        if not path.is_file() or path.is_symlink():
            raise OSError
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise E2EError(code, "configuration") from exc
    if not secret or "\n" in secret or "\r" in secret:
        raise E2EError(code, "configuration")
    return secret


def prepare_evidence_dir(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if path == Path(path.anchor):
        raise ValueError("EVIDENCE_DIR_ROOT_REJECTED")
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise ValueError("EVIDENCE_DIR_NOT_EMPTY")
    else:
        path.mkdir(parents=True, exist_ok=False)
    return path


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def atomic_json(directory: Path, name: str, value: Any) -> Path:
    target = directory / name
    if target.parent != directory or target.suffix != ".json":
        raise ValueError("EVIDENCE_PATH_INVALID")
    with NamedTemporaryFile("w", encoding="utf-8", dir=directory, prefix=".tmp-", suffix=".json", delete=False) as handle:
        handle.write(stable_json(value))
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return target


def redacted(value: Any) -> Any:
    """Remove sensitive fields and URL query/authority material recursively."""
    if isinstance(value, dict):
        return {str(key): "<redacted>" if _SENSITIVE_KEY.search(str(key)) else redacted(item) for key, item in sorted(value.items(), key=lambda row: str(row[0]))}
    if isinstance(value, list):
        return [redacted(item) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return value

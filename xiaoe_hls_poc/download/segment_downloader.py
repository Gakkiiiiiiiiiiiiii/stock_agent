"""单分片下载(13.6):有界重试、401/403 不重试、原子落盘。"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from ..config import MAX_SINGLE_SEGMENT_SIZE
from ..errors import ErrorCode, PocError
from ..http.response_validator import ensure_size_within, raise_for_classified_status
from ..http.retry_policy import RetryPolicy
from ..models import SegmentTask
from ..security.redactor import redact_url


def fetch_bytes(
    client: httpx.Client,
    url: str,
    *,
    kind: str = "SEGMENT",
    max_size: int = MAX_SINGLE_SEGMENT_SIZE,
) -> bytes:
    resp = client.get(url)
    raise_for_classified_status(resp, kind=kind)
    data = resp.content
    ensure_size_within(len(data), max_size, kind=kind)
    return data


def fetch_with_retry(
    client: httpx.Client,
    url: str,
    retry: RetryPolicy,
    *,
    kind: str = "SEGMENT",
) -> bytes:
    """带重试的字节获取;401/403 不重试(13.6)。"""
    last_exc: Exception | None = None
    for attempt in range(1, retry.max_attempts + 1):
        try:
            resp = client.get(url)
            if resp.status_code >= 400 and retry.is_retryable_status(resp.status_code):
                if attempt < retry.max_attempts:
                    time.sleep(retry.backoff(attempt))
                    continue
            raise_for_classified_status(resp, kind=kind)
            data = resp.content
            ensure_size_within(len(data), MAX_SINGLE_SEGMENT_SIZE, kind=kind)
            return data
        except PocError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not retry.is_retryable_exception(exc) or attempt >= retry.max_attempts:
                raise PocError(
                    ErrorCode.SEGMENT_HTTP_ERROR if kind != "KEY" else ErrorCode.KEY_HTTP_ERROR,
                    f"{kind} 请求失败: {redact_url(url)}",
                ) from exc
            time.sleep(retry.backoff(attempt))
    raise PocError(
        ErrorCode.SEGMENT_HTTP_ERROR, f"{kind} 重试耗尽: {redact_url(url)}"
    ) from last_exc


def decrypt_segment(
    task: SegmentTask,
    data: bytes,
    key_manager,
    iv_strategy: str = "hls-spec",
) -> bytes:
    """按需解密分片;METHOD=NONE 原样返回。"""
    from ..crypto.aes128 import decrypt_aes128_cbc
    from ..hls.iv_strategy import resolve_iv

    kc = task.key_context
    if kc is None or kc.method.upper() == "NONE":
        return data
    key = key_manager.resolve(kc)
    iv = resolve_iv(iv_strategy, kc.explicit_iv, task.media_sequence, task.index)
    return decrypt_aes128_cbc(key, iv, data)


def download_segment(
    client: httpx.Client,
    task: SegmentTask,
    dest: Path,
    retry: RetryPolicy,
) -> int:
    """下载单个分片到 dest(临时文件 + 原子改名)。返回字节数。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    last_exc: Exception | None = None
    for attempt in range(1, retry.max_attempts + 1):
        try:
            resp = client.get(task.uri_secret)
            if resp.status_code in (401, 403):
                raise PocError(
                    ErrorCode.SEGMENT_HTTP_ERROR,
                    f"分片 HTTP {resp.status_code}(不重试): {redact_url(task.uri_secret)}",
                )
            raise_for_classified_status(resp, kind="SEGMENT")
            data = resp.content
            ensure_size_within(len(data), MAX_SINGLE_SEGMENT_SIZE, kind="SEGMENT")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return len(data)
        except PocError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not retry.is_retryable_exception(exc):
                raise PocError(
                    ErrorCode.SEGMENT_HTTP_ERROR,
                    f"分片下载失败: {redact_url(task.uri_secret)}",
                ) from exc
            if attempt < retry.max_attempts:
                time.sleep(retry.backoff(attempt))
    raise PocError(
        ErrorCode.SEGMENT_HTTP_ERROR,
        f"分片下载重试耗尽: {redact_url(task.uri_secret)}",
    ) from last_exc

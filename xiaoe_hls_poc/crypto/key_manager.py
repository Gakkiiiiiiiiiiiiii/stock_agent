"""Key Manager:下载、校验、按脱敏哈希缓存 AES Key(14.4)。不持久化明文 Key。"""

from __future__ import annotations

from ..errors import ErrorCode, PocError
from ..models import KeyContext
from ..security.redactor import redact_url
from .aes128 import validate_key


class KeyManager:
    """Job 内 Key 缓存。key: key_id(脱敏 URI 哈希)-> 16 字节明文 Key(仅内存)。"""

    def __init__(self, fetch_bytes, *, max_keys: int = 64):
        # fetch_bytes(url: str) -> bytes,由调用方注入(带授权上下文)
        self._fetch = fetch_bytes
        self._cache: dict[str, bytes] = {}
        self._max_keys = max_keys

    def resolve(self, key_context: KeyContext) -> bytes:
        if key_context.method.upper() == "NONE":
            raise PocError(ErrorCode.INTERNAL_ERROR, "METHOD=NONE 无需 Key")
        if not key_context.uri_secret:
            raise PocError(ErrorCode.KEY_HTTP_ERROR, "EXT-X-KEY 缺少 URI")
        cache_key = key_context.key_id or key_context.uri_secret
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            data = self._fetch(key_context.uri_secret)
        except PocError:
            raise
        except Exception as exc:  # noqa: BLE001 - 转换为带码错误
            raise PocError(
                ErrorCode.KEY_HTTP_ERROR,
                f"Key 获取失败: {redact_url(key_context.uri_secret)}",
            ) from exc
        from ..media.segment_validator import looks_like_error_page

        if looks_like_error_page(data):
            raise PocError(
                ErrorCode.KEY_LENGTH_INVALID,
                "Key 响应疑似 HTML/JSON 错误页(检查授权与 URL 时效)",
            )
        key = validate_key(data)
        if len(self._cache) >= self._max_keys:
            self._cache.clear()
        self._cache[cache_key] = key
        return key

    @property
    def cached_count(self) -> int:
        return len(self._cache)

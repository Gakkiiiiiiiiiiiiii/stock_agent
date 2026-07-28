"""数据模型(设计文档第 9 节)。敏感字段不出现在 repr/序列化输出中。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---- 会话状态分类(10.8) ----
LOGIN_SESSION_VALID = "LOGIN_SESSION_VALID"
LOGIN_SESSION_EXPIRED = "LOGIN_SESSION_EXPIRED"
PLAYLIST_URL_VALID = "PLAYLIST_URL_VALID"
PLAYLIST_URL_EXPIRED = "PLAYLIST_URL_EXPIRED"
AUTH_CONTEXT_INCOMPLETE = "AUTH_CONTEXT_INCOMPLETE"


class BrowserProfile(BaseModel):
    model_config = ConfigDict(frozen=False)

    profile_name: str
    user_data_dir: str
    browser_type: str = "chromium"
    created_at: datetime = Field(default_factory=datetime.now)
    last_used_at: datetime = Field(default_factory=datetime.now)
    owner_uid: str = "local-user"


class AuthContext(BaseModel):
    """授权上下文。allowed_headers 中不得包含 Cookie 值。"""

    auth_context_id: str
    profile_name: str
    course_page_url: str = ""
    captured_at: datetime = Field(default_factory=datetime.now)
    storage_state_path: str | None = None
    allowed_headers: dict[str, str] = Field(default_factory=dict)
    cookie_jar_ref: str = ""  # 仅引用受保护存储,不含明文 Cookie
    user_agent: str = ""
    login_status: str = "UNKNOWN"


class CapturedMediaRequest(BaseModel):
    """捕获到的 M3U8 请求。明文 URL 不落盘进 capture.json。"""

    capture_id: str
    auth_context_id: str = ""
    page_url: str = ""
    playlist_url_redacted: str = ""
    method: str = "GET"
    response_status: int | None = None
    content_type: str | None = None
    captured_at: datetime = Field(default_factory=datetime.now)
    expires_at_hint: datetime | None = None
    candidate_score: float = 0.0
    has_authorization: bool = False
    # 明文 URL 仅存内存/受保护 secret 文件
    playlist_url_secret: str = Field(default="", repr=False, exclude=True)
    headers_secret_ref: str = Field(default="", repr=False)


class DownloadRequest(BaseModel):
    source_mode: str  # course_url / capture / manual / har
    course_url: str | None = None
    capture_id: str | None = None
    auth_context_id: str | None = None
    output_path: str = "output.mp4"
    quality: str = "best"
    engine: str = "python-managed"
    workers: int = 4
    retries: int = 4
    timeout_seconds: int = 30
    resume: bool = True
    iv_strategy: str = "hls-spec"
    refresh_on_expired: str = "none"
    authorized_content: bool = False
    probe_segments: int | None = None
    keep_temp: bool = False
    source_url_secret: str | None = Field(default=None, repr=False, exclude=True)


class VariantInfo(BaseModel):
    uri: str
    bandwidth: int | None = None
    average_bandwidth: int | None = None
    width: int | None = None
    height: int | None = None
    codecs: str | None = None
    audio_group: str | None = None
    selection_reason: str = ""


class KeyContext(BaseModel):
    """EXT-X-KEY 作用域快照。uri_secret 不进 repr/JSON。"""

    method: str = "NONE"
    explicit_iv_hex: str | None = None
    key_format: str = "identity"
    key_id: str = ""  # 脱敏 URI 的哈希
    uri_secret: str | None = Field(default=None, repr=False, exclude=True)

    @property
    def explicit_iv(self) -> bytes | None:
        if self.explicit_iv_hex is None:
            return None
        return bytes.fromhex(self.explicit_iv_hex)


class SegmentTask(BaseModel):
    index: int
    media_sequence: int
    duration: float = 0.0
    byte_range: tuple[int, int] | None = None
    key_context: KeyContext | None = None
    map_uri_secret: str | None = Field(default=None, repr=False, exclude=True)
    map_key_context: KeyContext | None = None
    discontinuity: bool = False
    local_encrypted_path: str = ""
    local_plain_path: str = ""
    uri_secret: str = Field(default="", repr=False, exclude=True)


class SegmentState(BaseModel):
    index: int
    status: str = "pending"  # pending/downloaded/decrypted/verified/failed
    attempts: int = 0
    bytes_downloaded: int = 0
    sha256: str | None = None
    uri_hash: str = ""
    error_code: str | None = None


class DownloadReport(BaseModel):
    job_id: str
    source_mode: str = ""
    auth_status: str = "UNKNOWN"
    capture_id: str | None = None
    engine: str = ""
    playlist_type: str = "unknown"
    encryption_method: str = "NONE"
    variant: VariantInfo | None = None
    segment_total: int = 0
    segment_success: int = 0
    segment_failed: int = 0
    estimated_duration: float = 0.0
    output_duration: float | None = None
    output_size: int | None = None
    validation_status: str = "UNKNOWN"
    refresh_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)

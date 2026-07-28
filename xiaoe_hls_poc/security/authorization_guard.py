"""合规守卫(2.3):授权确认 + 禁止的加密方式检查。"""

from __future__ import annotations

from ..errors import ErrorCode, PocError

COMPLIANCE_NOTICE = (
    "合规声明:本工具仅用于下载用户本人已购买或已获明确授权的内容,"
    "用于个人离线学习。不实现 DRM 绕过、登录绕过或批量抓取。"
)


def require_authorized_content(authorized: bool) -> None:
    """所有命令必须显式传入 --authorized-content。"""
    if not authorized:
        raise PocError(
            ErrorCode.AUTH_CONFIRM_REQUIRED,
            "缺少授权确认",
            hint="请确认内容为您本人已购买/已获授权,并添加 --authorized-content",
        )


def assert_encryption_supported(method: str, key_format: str = "identity") -> None:
    """SAMPLE-AES / 非 identity KEYFORMAT 立即停止(2.3 / 12.6)。"""
    m = (method or "NONE").upper()
    kf = (key_format or "identity").lower()
    if m == "SAMPLE-AES":
        raise PocError(
            ErrorCode.METHOD_UNSUPPORTED,
            "检测到 SAMPLE-AES,属于 DRM 范畴,本工具不支持也不绕过",
        )
    if m not in ("NONE", "AES-128"):
        raise PocError(
            ErrorCode.METHOD_UNSUPPORTED, f"不支持的加密方式: {method}"
        )
    if m == "AES-128" and kf != "identity":
        raise PocError(
            ErrorCode.KEYFORMAT_UNSUPPORTED,
            f"KEYFORMAT={key_format} 非 identity,可能为 DRM,停止",
        )

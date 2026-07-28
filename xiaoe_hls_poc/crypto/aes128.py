"""AES-128-CBC 解密与严格 PKCS7(设计文档第 14 节)。"""

from __future__ import annotations

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from ..errors import ErrorCode, PocError

BLOCK_SIZE = 16


def validate_key(key: bytes) -> bytes:
    if len(key) != 16:
        raise PocError(
            ErrorCode.KEY_LENGTH_INVALID,
            f"AES Key 长度为 {len(key)} 字节,应为 16 字节(可能取到错误页)",
        )
    return key


def validate_ciphertext(data: bytes) -> bytes:
    if not data or len(data) % BLOCK_SIZE != 0:
        raise PocError(
            ErrorCode.CIPHERTEXT_BLOCK_INVALID,
            f"密文长度 {len(data)} 不是 16 字节整数倍(可能不是媒体数据)",
        )
    return data


def decrypt_aes128_cbc(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """解密并严格 PKCS7 unpad。"""
    validate_key(key)
    if len(iv) != 16:
        raise PocError(ErrorCode.DECRYPT_PADDING_INVALID, "IV 必须为 16 字节")
    validate_ciphertext(ciphertext)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = cipher.decrypt(ciphertext)
    try:
        return unpad(padded, BLOCK_SIZE)
    except ValueError as exc:
        raise PocError(
            ErrorCode.DECRYPT_PADDING_INVALID,
            "PKCS7 unpad 失败,Key/IV 可能不正确",
        ) from exc


def strict_pkcs7_unpad(data: bytes) -> bytes:
    try:
        return unpad(data, BLOCK_SIZE)
    except ValueError as exc:
        raise PocError(ErrorCode.DECRYPT_PADDING_INVALID, "PKCS7 校验失败") from exc

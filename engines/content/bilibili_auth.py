from __future__ import annotations

import http.cookiejar
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx
import qrcode

BILIBILI_LOGIN_BASE_URL = "https://passport.bilibili.com"
BILIBILI_VERIFY_URL = "https://api.bilibili.com/x/web-interface/nav"
BILIBILI_COOKIE_DOMAINS = (".bilibili.com", ".bilibili.cn")
REQUIRED_BILIBILI_COOKIES = ("SESSDATA", "bili_jct", "DedeUserID")


class BilibiliAuthError(RuntimeError):
    pass


@dataclass(slots=True)
class CookieRecord:
    domain: str
    include_subdomains: bool
    path: str
    secure: bool
    expires_at: int
    name: str
    value: str
    http_only: bool = False


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in cookie_header.split(";"):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            cookies[name] = value
    return cookies


def build_cookie_records_from_header(cookie_header: str) -> list[CookieRecord]:
    cookies = parse_cookie_header(cookie_header)
    records: list[CookieRecord] = []
    for domain in BILIBILI_COOKIE_DOMAINS:
        for name, value in cookies.items():
            if not value:
                continue
            records.append(
                CookieRecord(
                    domain=domain,
                    include_subdomains=True,
                    path="/",
                    secure=True,
                    expires_at=0,
                    name=name,
                    value=value,
                    http_only=name in {"SESSDATA", "bili_jct"},
                )
            )
    return records


def write_cookie_file(records: Iterable[CookieRecord], output_path: str | Path) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File", ""]
    for record in records:
        prefix = "#HttpOnly_" if record.http_only else ""
        lines.append(
            "\t".join(
                [
                    f"{prefix}{record.domain}",
                    "TRUE" if record.include_subdomains else "FALSE",
                    record.path,
                    "TRUE" if record.secure else "FALSE",
                    str(int(record.expires_at or 0)),
                    record.name,
                    record.value,
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def create_cookie_file_from_header(cookie_header: str, output_path: str | Path) -> Path:
    records = build_cookie_records_from_header(cookie_header)
    if not records:
        raise BilibiliAuthError("No cookies were found in the provided Cookie header.")
    return write_cookie_file(records, output_path)


def collect_bilibili_cookie_records(cookie_jar: http.cookiejar.CookieJar) -> list[CookieRecord]:
    records: list[CookieRecord] = []
    for cookie in cookie_jar:
        if "bilibili." not in cookie.domain:
            continue
        if not cookie.value:
            continue
        records.append(
            CookieRecord(
                domain=cookie.domain,
                include_subdomains=cookie.domain.startswith("."),
                path=cookie.path or "/",
                secure=bool(cookie.secure),
                expires_at=int(cookie.expires or 0),
                name=cookie.name,
                value=cookie.value,
                http_only=bool(cookie.get_nonstandard_attr("HttpOnly")),
            )
        )
    return sorted(records, key=lambda item: (item.domain, item.path, item.name))


def collect_cookie_names(cookie_jar: http.cookiejar.CookieJar) -> set[str]:
    return {record.name for record in collect_bilibili_cookie_records(cookie_jar)}


def render_terminal_qr(data: str) -> str:
    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    lines: list[str] = []
    for row in matrix:
        lines.append("".join("██" if cell else "  " for cell in row))
    return "\n".join(lines)


class BilibiliQrLoginClient:
    def __init__(self, timeout: float = 10.0) -> None:
        self.client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
            },
            follow_redirects=True,
        )

    def create_qr(self) -> dict:
        response = self.client.get(f"{BILIBILI_LOGIN_BASE_URL}/x/passport-login/web/qrcode/generate")
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0 or not payload.get("data"):
            raise BilibiliAuthError(f"Failed to create Bilibili QR login: {payload}")
        return payload["data"]

    def poll(self, qrcode_key: str) -> dict:
        response = self.client.get(
            f"{BILIBILI_LOGIN_BASE_URL}/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": qrcode_key},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0 or not payload.get("data"):
            raise BilibiliAuthError(f"Failed to poll Bilibili QR login: {payload}")
        return payload["data"]

    def wait_for_login(self, qrcode_key: str, timeout_seconds: int = 180, interval_seconds: float = 2.0) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            data = self.poll(qrcode_key)
            state_code = int(data.get("code") or 0)
            if state_code == 0:
                confirm_url = data.get("url")
                if confirm_url:
                    self.client.get(confirm_url)
                return self._verify_login()
            if state_code == 86038:
                raise BilibiliAuthError("Bilibili QR code expired before confirmation.")
            if state_code in {86090, 86101}:
                time.sleep(interval_seconds)
                continue
            raise BilibiliAuthError(f"Unexpected Bilibili QR login state: {data}")
        raise BilibiliAuthError("Timed out waiting for Bilibili QR login confirmation.")

    def export_cookie_file(self, output_path: str | Path) -> Path:
        records = collect_bilibili_cookie_records(self.client.cookies.jar)
        cookie_names = {record.name for record in records}
        missing = [name for name in REQUIRED_BILIBILI_COOKIES if name not in cookie_names]
        if missing:
            raise BilibiliAuthError(
                "Bilibili login succeeded but required cookies were not found in the cookie jar: "
                + ", ".join(missing)
            )
        return write_cookie_file(records, output_path)

    def login_and_export(self, output_path: str | Path, timeout_seconds: int = 180) -> dict:
        qr_payload = self.create_qr()
        verify_payload = self.wait_for_login(qr_payload["qrcode_key"], timeout_seconds=timeout_seconds)
        cookie_path = self.export_cookie_file(output_path)
        return {
            "cookie_path": str(cookie_path),
            "verify": verify_payload,
            "qr_url": qr_payload["url"],
            "qrcode_key": qr_payload["qrcode_key"],
        }

    def _verify_login(self) -> dict:
        response = self.client.get(BILIBILI_VERIFY_URL)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        if payload.get("code") != 0 or not data.get("isLogin"):
            raise BilibiliAuthError(f"Bilibili login was not confirmed: {payload}")
        cookie_names = collect_cookie_names(self.client.cookies.jar)
        missing = [name for name in REQUIRED_BILIBILI_COOKIES if name not in cookie_names]
        if missing:
            raise BilibiliAuthError(f"Bilibili login is missing required cookies: {', '.join(missing)}")
        return {
            "uname": data.get("uname"),
            "mid": data.get("mid"),
            "vipStatus": data.get("vipStatus"),
            "isLogin": data.get("isLogin"),
        }

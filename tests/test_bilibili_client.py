from __future__ import annotations

import subprocess
from pathlib import Path

from engines.content.bilibili_client import BilibiliClient


def test_bilibili_client_prefers_cookie_file(monkeypatch, tmp_path):
    cookie_file = tmp_path / "bilibili.cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("BILIBILI_COOKIE_FILE", str(cookie_file))
    monkeypatch.delenv("BILIBILI_COOKIE_HEADER", raising=False)
    monkeypatch.delenv("BILIBILI_COOKIES_FROM_BROWSER", raising=False)
    client = BilibiliClient(yt_dlp_bin="yt-dlp")
    args = client._build_auth_args()
    assert "--cookies" in args
    assert str(cookie_file.resolve()) in args


def test_bilibili_client_uses_cookie_header(monkeypatch):
    monkeypatch.delenv("BILIBILI_COOKIE_FILE", raising=False)
    monkeypatch.setenv("BILIBILI_COOKIE_HEADER", "SESSDATA=test")
    monkeypatch.delenv("BILIBILI_COOKIES_FROM_BROWSER", raising=False)
    client = BilibiliClient(yt_dlp_bin="yt-dlp")
    args = client._build_auth_args()
    assert args == ["--add-header", "Referer:https://www.bilibili.com/", "--add-header", "Cookie:SESSDATA=test"]


def test_bilibili_client_falls_back_to_browser_cookie_spec(monkeypatch):
    monkeypatch.delenv("BILIBILI_COOKIE_FILE", raising=False)
    monkeypatch.delenv("BILIBILI_COOKIE_HEADER", raising=False)
    monkeypatch.setenv("BILIBILI_COOKIES_FROM_BROWSER", "edge")
    monkeypatch.setenv("BILIBILI_COOKIES_BROWSER_PROFILE", "Default")
    client = BilibiliClient(yt_dlp_bin="yt-dlp")
    args = client._build_auth_args()
    assert args == [
        "--add-header",
        "Referer:https://www.bilibili.com/",
        "--cookies-from-browser",
        "edge:Default",
    ]


def test_bilibili_client_download_audio_removes_stale_preview_file(monkeypatch, tmp_path):
    stale_file = tmp_path / "BVTEST123.wav"
    stale_file.write_bytes(b"stale-preview")

    client = BilibiliClient(yt_dlp_bin="yt-dlp")
    monkeypatch.setattr(
        client,
        "fetch_metadata",
        lambda url=None, bv_id=None: {"platform_video_id": "BVTEST123"},
    )

    def fake_run_command(command):
        assert not stale_file.exists()
        template = Path(command[command.index("-o") + 1])
        output_file = Path(str(template).replace("%(ext)s", "wav"))
        output_file.write_bytes(b"fresh-full-audio")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(BilibiliClient, "_run_command", staticmethod(fake_run_command))
    audio_path = client.download_audio(tmp_path, url="https://www.bilibili.com/video/BVTEST123")
    assert audio_path == tmp_path / "BVTEST123.wav"
    assert audio_path.read_bytes() == b"fresh-full-audio"

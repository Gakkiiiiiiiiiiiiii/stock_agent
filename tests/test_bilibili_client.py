from __future__ import annotations

from engines.content.bilibili_client import BilibiliClient


def test_bilibili_client_falls_back_to_python_module(monkeypatch):
    monkeypatch.setattr("engines.content.bilibili_client.shutil.which", lambda _: None)
    client = BilibiliClient()

    command = client._yt_dlp_command()

    assert command[1:] == ["-m", "yt_dlp"]

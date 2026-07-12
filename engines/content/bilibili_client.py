from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


class BilibiliClient:
    def __init__(self, yt_dlp_bin: str | None = None) -> None:
        self.yt_dlp_bin = yt_dlp_bin or os.getenv("YT_DLP_BIN", "yt-dlp")
        self.ffmpeg_bin = os.getenv("FFMPEG_BIN")
        self.cookie_file = os.getenv("BILIBILI_COOKIE_FILE")
        self.cookie_header = os.getenv("BILIBILI_COOKIE_HEADER")
        self.cookies_from_browser = os.getenv("BILIBILI_COOKIES_FROM_BROWSER")
        self.cookies_browser_profile = os.getenv("BILIBILI_COOKIES_BROWSER_PROFILE")

    def build_watch_url(self, bv_id: str) -> str:
        return f"https://www.bilibili.com/video/{bv_id}"

    def resolve_source(self, url: str | None = None, bv_id: str | None = None) -> tuple[str, str | None]:
        if bv_id:
            return self.build_watch_url(bv_id), bv_id
        if not url:
            raise ValueError("url or bv_id is required")
        return url, self.extract_bv_id(url)

    def extract_bv_id(self, url: str) -> str | None:
        match = re.search(r"(BV[0-9A-Za-z]+)", url)
        return match.group(1) if match else None

    def fetch_metadata(self, url: str | None = None, bv_id: str | None = None) -> dict:
        source_url, parsed_bv = self.resolve_source(url=url, bv_id=bv_id)
        payload = self._run_json_command([self.yt_dlp_bin, "--dump-single-json", "--no-playlist", *self._build_auth_args(), source_url])
        return {
            "platform": "bilibili",
            "platform_video_id": str(payload.get("id") or parsed_bv or ""),
            "bvid": parsed_bv or str(payload.get("id") or ""),
            "url": payload.get("webpage_url") or source_url,
            "title": payload.get("title") or parsed_bv or "unknown",
            "author_name": payload.get("uploader") or payload.get("channel") or "",
            "author_id": str(payload.get("uploader_id") or payload.get("channel_id") or ""),
            "publish_time": payload.get("upload_date"),
            "duration_seconds": int(payload.get("duration") or 0),
            "cover_url": payload.get("thumbnail") or "",
            "description": payload.get("description") or "",
        }

    def download_audio(self, output_dir: str | Path, url: str | None = None, bv_id: str | None = None) -> Path:
        source_url, parsed_bv = self.resolve_source(url=url, bv_id=bv_id)
        metadata = self.fetch_metadata(url=source_url)
        video_id = metadata["platform_video_id"] or parsed_bv or "bilibili_audio"
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        for stale_file in output_path.glob(f"{video_id}.*"):
            stale_file.unlink(missing_ok=True)
        template = str(output_path / f"{video_id}.%(ext)s")
        command = [
            self.yt_dlp_bin,
            "--no-playlist",
            "--force-overwrites",
            "--no-continue",
            "-x",
            "--audio-format",
            "wav",
            "--audio-quality",
            "0",
        ]
        if self.ffmpeg_bin:
            command.extend(["--ffmpeg-location", str(Path(self.ffmpeg_bin).resolve().parent)])
        command.extend(
            [
                *self._build_auth_args(),
                "-o",
                template,
                source_url,
            ]
        )
        self._run_command(command)
        matches = sorted(output_path.glob(f"{video_id}.*"))
        if not matches:
            raise FileNotFoundError(f"downloaded audio not found for {video_id}")
        return matches[0]

    def download_video(self, output_dir: str | Path, url: str | None = None, bv_id: str | None = None) -> Path:
        source_url, parsed_bv = self.resolve_source(url=url, bv_id=bv_id)
        metadata = self.fetch_metadata(url=source_url)
        video_id = metadata["platform_video_id"] or parsed_bv or "bilibili_video"
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        for stale_file in output_path.glob(f"{video_id}.*"):
            stale_file.unlink(missing_ok=True)
        template = str(output_path / f"{video_id}.%(ext)s")
        command = [
            self.yt_dlp_bin,
            "--no-playlist",
            "--force-overwrites",
            "--no-continue",
            "-f",
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "--merge-output-format",
            "mp4",
        ]
        if self.ffmpeg_bin:
            command.extend(["--ffmpeg-location", str(Path(self.ffmpeg_bin).resolve().parent)])
        command.extend(
            [
                *self._build_auth_args(),
                "-o",
                template,
                source_url,
            ]
        )
        self._run_command(command)
        matches = sorted(output_path.glob(f"{video_id}.*"))
        if not matches:
            raise FileNotFoundError(f"downloaded video not found for {video_id}")
        prioritized = [match for match in matches if match.suffix.lower() in {".mp4", ".mkv", ".webm"}]
        return (prioritized or matches)[0]

    def describe_auth_source(self) -> str:
        if self.cookie_file and Path(self.cookie_file).expanduser().exists():
            return f"cookie_file:{Path(self.cookie_file).expanduser().resolve()}"
        if self.cookie_header:
            return "cookie_header"
        if self.cookies_from_browser:
            if self.cookies_browser_profile:
                return f"browser:{self.cookies_from_browser}:{self.cookies_browser_profile}"
            return f"browser:{self.cookies_from_browser}"
        return "anonymous"

    def _build_auth_args(self) -> list[str]:
        command = ["--add-header", "Referer:https://www.bilibili.com/"]
        if self.cookie_file:
            cookie_path = Path(self.cookie_file).expanduser()
            if cookie_path.exists():
                command.extend(["--cookies", str(cookie_path.resolve())])
                return command
        if self.cookie_header:
            command.extend(["--add-header", f"Cookie:{self.cookie_header}"])
            return command
        if self.cookies_from_browser:
            browser_spec = self.cookies_from_browser
            if self.cookies_browser_profile:
                browser_spec = f"{browser_spec}:{self.cookies_browser_profile}"
            command.extend(["--cookies-from-browser", browser_spec])
        return command

    @staticmethod
    def _run_json_command(command: list[str]) -> dict:
        result = BilibiliClient._run_command(command)
        return json.loads(result.stdout)

    @staticmethod
    def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or str(exc)
            raise RuntimeError(details) from exc

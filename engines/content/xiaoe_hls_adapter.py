from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse


class XiaoeHlsAdapter:
    def build_metadata(
        self,
        *,
        m3u8_url: str,
        page_url: str | None = None,
        title: str | None = None,
        author_name: str | None = None,
        publish_time: str | None = None,
        duration_seconds: int | None = None,
        platform_video_id: str | None = None,
        cover_url: str | None = None,
        description: str | None = None,
    ) -> dict:
        resolved_id = platform_video_id or self.extract_video_id(page_url or "") or self.extract_video_id(m3u8_url)
        if not resolved_id:
            resolved_id = hashlib.sha1(m3u8_url.encode("utf-8")).hexdigest()[:16]
        return {
            "platform": "xiaoe",
            "platform_video_id": resolved_id,
            "bvid": None,
            "url": page_url or self._redacted_url(m3u8_url),
            "title": title or resolved_id,
            "author_name": author_name or "",
            "author_id": "",
            "publish_time": publish_time,
            "duration_seconds": duration_seconds or 0,
            "cover_url": cover_url or "",
            "description": description or "",
        }

    def download_video(
        self,
        output_dir: str | Path,
        *,
        m3u8_url: str,
        page_url: str | None = None,
        headers: dict[str, str] | None = None,
        output_stem: str | None = None,
        engine: str = "ffmpeg-direct",
        quality: str = "best",
        workers: int = 4,
        timeout_seconds: int = 30,
    ) -> Path:
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = self._safe_stem(output_stem or self.extract_video_id(m3u8_url) or hashlib.sha1(m3u8_url.encode("utf-8")).hexdigest()[:16])
        target = target_dir / f"{stem}.mp4"
        from xiaoe_hls_poc.input.request_loader import EffectiveContext

        ctx = EffectiveContext(
            source_mode="manual",
            url_secret=m3u8_url,
            headers=headers or {},
            course_url=page_url,
            page_url=page_url or "",
        )
        if engine == "ffmpeg-direct":
            from xiaoe_hls_poc.engines.ffmpeg_direct import run_ffmpeg_direct

            run_ffmpeg_direct(ctx, target, timeout=max(timeout_seconds, 3600))
        elif engine == "python-managed":
            from xiaoe_hls_poc.engines.python_managed import run_python_managed
            from xiaoe_hls_poc.models import DownloadRequest

            request = DownloadRequest(
                source_mode="manual",
                course_url=page_url,
                output_path=str(target),
                quality=quality,
                engine=engine,
                workers=workers,
                timeout_seconds=timeout_seconds,
                authorized_content=True,
            )
            run_python_managed(ctx, request)
        else:
            raise ValueError("engine must be ffmpeg-direct or python-managed")
        return target

    @staticmethod
    def extract_video_id(value: str) -> str | None:
        match = re.search(r"(v_[0-9A-Za-z]+)", value or "")
        return match.group(1) if match else None

    @staticmethod
    def _redacted_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else url

    @staticmethod
    def _safe_stem(value: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
        return cleaned or "xiaoe_hls"

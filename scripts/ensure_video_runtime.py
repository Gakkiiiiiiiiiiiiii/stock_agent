from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _resolve_binary(env_name: str, fallback: str) -> str | None:
    configured = str(os.getenv(env_name, "")).strip()
    if configured:
        path = Path(configured)
        if path.exists():
            return str(path)
    return shutil.which(fallback)


def main() -> int:
    ffmpeg_path = _resolve_binary("FFMPEG_BIN", "ffmpeg")
    ffprobe_path = _resolve_binary("FFPROBE_BIN", "ffprobe")
    yt_dlp_path = _resolve_binary("YT_DLP_BIN", "yt-dlp")
    missing = []
    if not ffmpeg_path:
        missing.append("ffmpeg")
    if not ffprobe_path:
        missing.append("ffprobe")
    if not yt_dlp_path:
        missing.append("yt-dlp")
    if not missing:
        print(
            "video runtime ready:",
            f"ffmpeg={ffmpeg_path}",
            f"ffprobe={ffprobe_path}",
            f"yt-dlp={yt_dlp_path}",
        )
        return 0

    message = (
        "Missing required video runtime binaries: "
        + ", ".join(missing)
        + ". Rebuild the Docker images so the Debian ffmpeg package is installed "
        + "and ensure docker-compose passes FFMPEG_BIN=/usr/bin/ffmpeg and FFPROBE_BIN=/usr/bin/ffprobe."
    )
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

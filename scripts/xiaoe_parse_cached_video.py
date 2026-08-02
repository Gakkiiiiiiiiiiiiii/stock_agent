from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

# xiaoe_hls_poc 未包含在 editable 安装中，直接运行脚本时需要项目根目录在 sys.path 上
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from engines.content.asr_service import AsrService
from engines.content.audio_pipeline import AudioPipeline
from engines.content.video_ingest_service import VideoIngestService
from storage.bootstrap import create_all


DEFAULT_CACHE = "storage/runtime/xiaoe_batch_cache/play_urls.private.json"
DEFAULT_OUT_ROOT = "storage/runtime"


def _load_cache(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"缓存格式不正确: {path}")
    return [row for row in data if isinstance(row, dict)]


def _select_video(rows: list[dict[str, Any]], resource_id: str | None, date: str | None, title_contains: str | None) -> dict[str, Any]:
    candidates = rows
    if resource_id:
        candidates = [row for row in candidates if row.get("resource_id") == resource_id]
    if date:
        normalized = date.replace("-", ".")
        candidates = [
            row
            for row in candidates
            if normalized in str(row.get("start_at") or "")
            or date in str(row.get("title") or "")
            or date.replace("-", "") in str(row.get("title") or "")
        ]
    if title_contains:
        candidates = [row for row in candidates if title_contains in str(row.get("title") or "")]
    if not candidates:
        raise LookupError("缓存中没有匹配的视频")
    return candidates[0]


def _safe_stem(row: dict[str, Any]) -> str:
    date = str(row.get("start_at") or "").replace(".", "")
    resource_id = str(row.get("resource_id") or "xiaoe")
    return f"xiaoe_{date or resource_id[-8:]}"


def _download(url: str, out: Path, referer: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    }
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=180) as response:
        response.raise_for_status()
        with out.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                if chunk:
                    handle.write(chunk)


def _write_summary(path: Path, detail: dict[str, Any], transcript: dict[str, Any]) -> None:
    units = detail.get("knowledge_units") or []
    counts: dict[tuple[str, str], int] = {}
    for unit in units:
        key = (str(unit.get("primary_domain") or "UNKNOWN"), str(unit.get("knowledge_kind") or "UNKNOWN"))
        counts[key] = counts.get(key, 0) + 1
    top_lines = []
    for unit in units[:30]:
        statement = str(unit.get("canonical_statement") or unit.get("statement") or "").strip()
        if not statement:
            continue
        if len(statement) > 180:
            statement = statement[:180] + "..."
        top_lines.append(f"- [{unit.get('primary_domain')}/{unit.get('knowledge_kind')}] {statement}")
    analysis = detail.get("analysis_document") or {}
    title = (detail.get("asset") or {}).get("title") or "小鹅通视频解析"
    markdown = [
        f"# {title}",
        "",
        "## 解析结果",
        f"- 时长：约 {float(transcript.get('duration_seconds') or 0) / 60:.1f} 分钟",
        f"- ASR 片段：{len(transcript.get('segments') or [])}",
        f"- 自动章节：{len(detail.get('chapters') or [])}",
        f"- 知识单元：{len(units)}",
        "",
        "## 自动摘要",
        str(analysis.get("core_summary") or ""),
        "",
        "## 知识类型分布",
    ]
    markdown.extend(f"- {domain}/{kind}: {count}" for (domain, kind), count in sorted(counts.items()))
    markdown.extend(["", "## 知识单元摘录", *top_lines, "", "仅为本人已授权课程内容的学习摘要，不构成投资建议。"])
    path.write_text("\n".join(markdown), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从批量缓存的小鹅通播放信息解析单个视频。")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--resource-id")
    parser.add_argument("--date")
    parser.add_argument("--title-contains")
    parser.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    parser.add_argument("--language-hint", default="zh")
    parser.add_argument("--index-knowledge", action="store_true")
    parser.add_argument("--enable-visual", action="store_true", help="下载整段视频并抽取视觉帧（需要缓存中有未过期的 hls_url）")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args()
    rows = _load_cache(Path(args.cache))
    row = _select_video(rows, args.resource_id, args.date, args.title_contains)
    stem = _safe_stem(row)
    out_dir = Path(args.out_root) / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    mp3_url = row.get("mp3_url")
    if not mp3_url:
        raise RuntimeError("匹配视频没有 mp3_url，暂不解析")
    source_mp3 = out_dir / "source.mp3"
    if not source_mp3.is_file():
        _download(str(mp3_url), source_mp3, referer=str(row.get("page_url") or "https://appaoswidcd4711.h5.xiaoeknow.com/"))

    audio_pipeline = AudioPipeline()
    wav_path = audio_pipeline.standardize_audio(source_mp3, out_dir)
    asr = AsrService()
    transcript_path = out_dir / "transcript.json"
    if transcript_path.is_file():
        logging.getLogger(__name__).info("复用已有转写: %s", transcript_path)
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    else:
        transcript = asr.transcribe(wav_path, language_hint=args.language_hint)
        transcript["source_audio"] = str(source_mp3)
        transcript["standardized_audio"] = str(wav_path)
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")

    create_all()
    service = VideoIngestService(audio_pipeline=audio_pipeline, asr_service=asr)
    metadata = {
        "platform": "xiaoe",
        "platform_video_id": row.get("resource_id"),
        "bvid": None,
        "url": row.get("page_url") or "",
        "title": row.get("title") or row.get("resource_id") or "小鹅通视频",
        "author_name": row.get("author_name"),
        "author_id": None,
        "publish_time": str(row.get("start_at") or "").replace(".", "-"),
        "duration_seconds": row.get("duration_seconds") or int(float(transcript.get("duration_seconds") or 0)),
        "cover_url": row.get("cover_url"),
        "description": "小鹅通课程视频，基于已授权登录播放获取的音频转写。",
    }
    asset = service.video_repo.upsert_metadata(metadata)
    service.video_repo.update_audio(asset.id, str(wav_path.resolve()))
    transcript = service.transcript_postprocessor.normalize(transcript, metadata=metadata)
    transcript["source_hash"] = hashlib.sha256((transcript.get("text") or "").encode("utf-8")).hexdigest()
    service.video_repo.save_transcript(asset.id, transcript)
    task = service.task_repo.create(
        source_type="xiaoe_cached_audio",
        source_ref=metadata["url"],
        options={"title": metadata["title"], "parser": "v3.0-rule"},
        video_id=asset.id,
    )
    frame_insights: list[dict[str, Any]] = []
    if args.enable_visual:
        hls_url = row.get("hls_url")
        if not hls_url:
            raise RuntimeError("匹配视频没有 hls_url，无法抽取视觉帧；请刷新播放信息缓存后重试")
        visual_headers = {
            "Referer": str(row.get("page_url") or "https://appaoswidcd4711.h5.xiaoeknow.com/"),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        }
        visual_bundle = service._build_xiaoe_visual_context(
            metadata=metadata,
            transcript=transcript,
            m3u8_url=str(hls_url),
            headers=visual_headers,
            video_id=asset.id,
        )
        if visual_bundle is None:
            raise RuntimeError("视觉上下文构建失败：hls_url 可能已过期或视频下载/抽帧失败，请刷新播放信息缓存后重试（详见告警日志）")
        frame_insights = visual_bundle.get("frame_insights") or []
    knowledge_result = service._build_video_knowledge(
        task_id=task.id,
        metadata=metadata,
        video_id=asset.id,
        transcript=transcript,
        frame_insights=frame_insights,
        index_knowledge=bool(args.index_knowledge),
    )
    service.task_repo.update(task.id, status="success", stage="success", progress=100, video_id=asset.id)
    detail = service.query_repo.get_video_detail(asset.id) or {}
    detail.update({"task": service.task_repo.serialize(service.task_repo.get(task.id)), "knowledge_result": knowledge_result})
    detail_path = out_dir / "detail.json"
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary_path = out_dir / "video_summary.md"
    _write_summary(summary_path, detail, transcript)
    print(json.dumps({
        "video_id": asset.id,
        "task_id": task.id,
        "title": metadata["title"],
        "out_dir": str(out_dir),
        "transcript": str(transcript_path),
        "detail": str(detail_path),
        "summary": str(summary_path),
        "chapters": len(detail.get("chapters") or []),
        "knowledge_units": len(detail.get("knowledge_units") or []),
        "frame_insights": len(frame_insights),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

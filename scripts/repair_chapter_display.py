"""离线修复已解析视频的章节标题/实体/摘要与分析文档。

不重跑下载/ASR/视觉：用 runtime 目录缓存的 transcript.json + DB 帧记录重建章节，
用新代码重算标题与实体，用知识单元重算章节摘要，并用 LLM 重新生成分析文档。
用法（容器内）: python scripts/repair_chapter_display.py --video-id 9
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from engines.content.video_analysis_document_generator import VideoAnalysisDocumentGenerator
from engines.content.video_ingest_service import VideoIngestService
from storage.db import SessionLocal


def _load_transcript(runtime_root: Path, publish_time: str | None) -> dict | None:
    date = "".join(ch for ch in str(publish_time or "") if ch.isdigit())[:8]
    if not date:
        return None
    path = runtime_root / f"xiaoe_{date}" / "transcript.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def repair_video(service: VideoIngestService, video_id: int, runtime_root: Path) -> dict:
    with SessionLocal() as session:
        asset = session.execute(
            text("SELECT id, title, publish_time_raw, platform, platform_video_id FROM video_asset WHERE id=:vid"),
            {"vid": video_id},
        ).fetchone()
        if asset is None:
            raise LookupError(f"video {video_id} not found")
        metadata = {
            "title": asset[1],
            "publish_time": asset[2],
            "platform": asset[3],
            "platform_video_id": asset[4],
        }
        db_chapters = session.execute(
            text("SELECT id, chapter_index FROM video_chapter WHERE video_id=:vid ORDER BY chapter_index"),
            {"vid": video_id},
        ).fetchall()
        frame_rows = session.execute(
            text(
                "SELECT timestamp_ms, image_path, trigger_source, ocr_text, visual_summary, related_text, symbols_json "
                "FROM video_frame WHERE video_id=:vid ORDER BY frame_index"
            ),
            {"vid": video_id},
        ).fetchall()

    transcript = _load_transcript(runtime_root, metadata["publish_time"])
    if transcript is None:
        raise FileNotFoundError(f"transcript.json 不存在（publish_time={metadata['publish_time']}），跳过")

    chapter_ids = [row[0] for row in db_chapters]
    chapter_index_by_id = {row[0]: int(row[1]) for row in db_chapters}
    units = service.knowledge_repo.list_units_for_video(video_id)
    for unit in units:
        if unit.get("chapter_index") is None and unit.get("source_chapter_id") is not None:
            unit["chapter_index"] = chapter_index_by_id.get(int(unit["source_chapter_id"]), 0)
    frame_insights = [
        {
            "timestamp_ms": row[0],
            "image_path": row[1],
            "trigger_source": row[2],
            "ocr_text": row[3],
            "visual_summary": row[4],
            "related_text": row[5],
            "symbols": json.loads(row[6] or "[]"),
        }
        for row in frame_rows
    ]
    windows = service.temporal_window_builder.build(transcript=transcript, frame_insights=frame_insights)
    chapters = service.chapter_segmenter.segment(windows)
    if len(chapters) != len(db_chapters):
        raise RuntimeError(f"重建章节数 {len(chapters)} 与库中 {len(db_chapters)} 不一致，放弃修复 video={video_id}")

    # 章节摘要：优先用知识单元（LLM 精炼语句），避免口播原文
    summaries = VideoAnalysisDocumentGenerator._fallback_chapter_summaries(chapters, units)
    summary_by_index = {int(item["chapter_index"]): item["summary"] for item in summaries}

    with SessionLocal() as session:
        for chapter, row_id in zip(chapters, chapter_ids):
            session.execute(
                text("UPDATE video_chapter SET title=:title, entities_json=:entities, summary=:summary WHERE id=:cid"),
                {
                    "title": chapter["title"],
                    "entities": json.dumps(chapter.get("entities") or [], ensure_ascii=False),
                    "summary": summary_by_index.get(int(chapter["chapter_index"]), chapter.get("summary") or ""),
                    "cid": row_id,
                },
            )
        session.commit()

    # 分析文档：LLM 重新生成（章节摘要已由 _apply_chapter_summaries 覆盖为新值）
    service._apply_chapter_summaries(chapters, summaries)
    generator = VideoAnalysisDocumentGenerator()
    payload = generator.generate(metadata=metadata, chapters=chapters, units=units)
    service.analysis_document_repo.upsert(video_id, payload)
    # LLM 精炼出的章节摘要（若有）覆盖无知识单元章节的口播原文兜底
    service._apply_chapter_summaries(chapters, payload.get("chapter_summaries") or [])
    with SessionLocal() as session:
        for chapter, row_id in zip(chapters, chapter_ids):
            session.execute(
                text("UPDATE video_chapter SET summary=:summary WHERE id=:cid"),
                {"summary": chapter.get("summary") or "", "cid": row_id},
            )
        session.commit()
    return {
        "video_id": video_id,
        "chapters": len(chapters),
        "titles": [chapter["title"] for chapter in chapters],
        "generator_version": payload.get("generator_version"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", type=int, action="append", required=True)
    parser.add_argument("--runtime-root", default="storage/runtime")
    args = parser.parse_args()
    service = VideoIngestService()
    for video_id in args.video_id:
        try:
            result = repair_video(service, video_id, Path(args.runtime_root))
            print(json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"video_id": video_id, "error": str(exc)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

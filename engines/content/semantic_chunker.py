from __future__ import annotations

import os
import re


TOPIC_SHIFT_MARKERS = (
    "接下来",
    "再看",
    "然后",
    "另一方面",
    "第二个",
    "第三个",
    "最后",
    "总结一下",
)


class SemanticChunker:
    def __init__(
        self,
        target_duration_seconds: int | None = None,
        min_duration_seconds: int | None = None,
        overlap_seconds: int | None = None,
    ) -> None:
        self.target_duration_ms = int(os.getenv("VIDEO_CHUNK_TARGET_SECONDS", str(target_duration_seconds or 90))) * 1000
        self.min_duration_ms = int(os.getenv("VIDEO_CHUNK_MIN_SECONDS", str(min_duration_seconds or 45))) * 1000
        self.overlap_ms = int(os.getenv("VIDEO_CHUNK_OVERLAP_SECONDS", str(overlap_seconds or 12))) * 1000

    def build(self, transcript: dict, frame_insights: list[dict] | None = None) -> list[dict]:
        segments = [segment for segment in transcript.get("segments") or [] if str(segment.get("text") or "").strip()]
        if not segments:
            return []
        frames = sorted(frame_insights or [], key=lambda item: int(item.get("timestamp_ms") or 0))
        chunks: list[dict] = []
        current_segments: list[dict] = []
        current_start_ms: int | None = None
        current_end_ms: int | None = None

        def flush_chunk() -> None:
            nonlocal current_segments, current_start_ms, current_end_ms
            if not current_segments:
                return
            chunks.append(
                self._build_chunk_payload(
                    chunk_index=len(chunks),
                    segments=current_segments,
                    start_ms=current_start_ms or 0,
                    end_ms=current_end_ms or current_segments[-1].get("end_ms") or 0,
                    frames=frames,
                )
            )
            if self.overlap_ms > 0:
                overlap_segments = []
                for segment in reversed(current_segments):
                    overlap_segments.insert(0, segment)
                    overlap_span = int(current_end_ms or 0) - int(overlap_segments[0].get("start_ms") or 0)
                    if overlap_span >= self.overlap_ms:
                        break
                current_segments = overlap_segments
                current_start_ms = int(current_segments[0].get("start_ms") or 0) if current_segments else None
                current_end_ms = int(current_segments[-1].get("end_ms") or 0) if current_segments else None
            else:
                current_segments = []
                current_start_ms = None
                current_end_ms = None

        for segment in segments:
            start_ms = int(segment.get("start_ms") or 0)
            end_ms = int(segment.get("end_ms") or start_ms)
            text = str(segment.get("text") or "").strip()
            if current_start_ms is None:
                current_start_ms = start_ms
            projected_start = current_start_ms
            projected_end = end_ms
            projected_duration = projected_end - projected_start
            should_split = False
            if current_segments and projected_duration >= self.target_duration_ms:
                should_split = True
            elif current_segments and projected_duration >= self.min_duration_ms and self._looks_like_topic_shift(text):
                should_split = True
            if should_split:
                flush_chunk()
                if not current_segments:
                    current_start_ms = start_ms
            current_segments.append(segment)
            current_end_ms = end_ms

        if current_segments:
            chunks.append(
                self._build_chunk_payload(
                    chunk_index=len(chunks),
                    segments=current_segments,
                    start_ms=current_start_ms or 0,
                    end_ms=current_end_ms or current_segments[-1].get("end_ms") or 0,
                    frames=frames,
                )
            )
        return chunks

    def _build_chunk_payload(self, chunk_index: int, segments: list[dict], start_ms: int, end_ms: int, frames: list[dict]) -> dict:
        chunk_frames = [
            frame
            for frame in frames
            if start_ms <= int(frame.get("timestamp_ms") or 0) <= end_ms
        ]
        transcript_text = " ".join(str(segment.get("text") or "").strip() for segment in segments if str(segment.get("text") or "").strip()).strip()
        ocr_texts = [str(frame.get("ocr_text") or "").strip() for frame in chunk_frames if str(frame.get("ocr_text") or "").strip()]
        visual_focus_parts = [str(frame.get("visual_summary") or "").strip() for frame in chunk_frames if str(frame.get("visual_summary") or "").strip()]
        visual_tags = sorted({tag for frame in chunk_frames for tag in self._frame_tags(frame)})
        entities = sorted({entity for frame in chunk_frames for entity in self._frame_entities(frame)})
        return {
            "chunk_index": chunk_index,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "topic": self._infer_topic(transcript_text, ocr_texts),
            "transcript_text": transcript_text,
            "ocr_text": " | ".join(ocr_texts[:6]).strip(),
            "visual_focus": " | ".join(visual_focus_parts[:4]).strip(),
            "visual_tags": visual_tags,
            "entities": entities,
            "confidence_score": self._estimate_confidence(segments=segments, chunk_frames=chunk_frames),
            "frame_refs": [
                {
                    "frame_index": frame.get("frame_index"),
                    "timestamp_ms": frame.get("timestamp_ms"),
                    "image_path": frame.get("image_path"),
                    "ocr_text": frame.get("ocr_text"),
                    "visual_summary": frame.get("visual_summary"),
                }
                for frame in chunk_frames
            ],
        }

    @staticmethod
    def _looks_like_topic_shift(text: str) -> bool:
        normalized = re.sub(r"\s+", "", text)
        return any(marker in normalized for marker in TOPIC_SHIFT_MARKERS)

    @staticmethod
    def _infer_topic(transcript_text: str, ocr_texts: list[str]) -> str:
        source = " ".join([transcript_text, *ocr_texts]).strip()
        if not source:
            return "未分类片段"
        match = re.search(r"([A-Za-z0-9\.\-]{4,12}\.(?:HK|US)|\d{6}|上证指数|深证成指|创业板|恒生科技|黄金|原油|美联储|半导体|AI)", source, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()
        for marker in ("支撑", "压力", "突破", "反弹", "下跌", "风险", "催化", "业绩", "估值"):
            if marker in source:
                return marker
        snippet = re.sub(r"\s+", " ", source).strip()
        return snippet[:32] or "未分类片段"

    @staticmethod
    def _frame_tags(frame: dict) -> list[str]:
        tags: list[str] = []
        summary = str(frame.get("visual_summary") or "")
        ocr_text = str(frame.get("ocr_text") or "")
        merged = f"{summary} {ocr_text}"
        if any(token in merged for token in ("K线", "均线", "MACD", "分时", "日线", "周线")):
            tags.append("candlestick_chart")
        if any(token in merged for token in ("表", "亿元", "营收", "利润", "毛利率")):
            tags.append("financial_table")
        if any(token in merged for token in ("PPT", "标题", "目录", "这一页")):
            tags.append("presentation_slide")
        if ocr_text:
            tags.append("subtitle")
        return tags

    @staticmethod
    def _frame_entities(frame: dict) -> list[str]:
        entities = []
        entities.extend(str(item).strip() for item in frame.get("symbols") or [] if str(item).strip())
        ocr_text = str(frame.get("ocr_text") or "")
        entities.extend(re.findall(r"\b\d{4}\.HK\b|\b\d{6}\b", ocr_text, flags=re.IGNORECASE))
        return entities

    @staticmethod
    def _estimate_confidence(segments: list[dict], chunk_frames: list[dict]) -> float:
        segment_scores = [float(segment.get("confidence_score") or 0.7) for segment in segments]
        frame_scores = [float(frame.get("confidence_score") or 0.6) for frame in chunk_frames]
        scores = segment_scores + frame_scores
        if not scores:
            return 0.5
        return round(sum(scores) / len(scores), 4)

from __future__ import annotations

import hashlib
import os
import re

from engines.content.chapter_classifier import ChapterClassifier


class ChapterSegmenter:
    def __init__(
        self,
        classifier: ChapterClassifier | None = None,
        min_chapter_seconds: int = 30,
        max_chapter_seconds: int | None = None,
        max_chapter_chars: int | None = None,
    ) -> None:
        self.classifier = classifier or ChapterClassifier()
        self.min_chapter_ms = min_chapter_seconds * 1000
        self.max_chapter_ms = int(
            os.getenv("VIDEO_KNOWLEDGE_CHAPTER_MAX_SECONDS", str(max_chapter_seconds or 420))
        ) * 1000
        self.max_chapter_chars = int(
            os.getenv("VIDEO_KNOWLEDGE_CHAPTER_MAX_CHARS", str(max_chapter_chars or 5200))
        )

    def segment(self, windows: list[dict]) -> list[dict]:
        if not windows:
            return []
        chapters: list[dict] = []
        current: list[dict] = []
        current_chars = 0
        last_domain: str | None = None

        for window in windows:
            classification = self.classifier.classify(window.get("transcript_text", ""), window.get("ocr_text", ""), window.get("visual_summary", ""))
            boundary_score = self._boundary_score(current[-1] if current else None, window, last_domain, classification["primary_domain"])
            window_chars = len(str(window.get("transcript_text") or ""))
            forced_boundary = bool(current) and (
                int(window.get("end_ms") or 0) - int(current[0].get("start_ms") or 0) > self.max_chapter_ms
                or current_chars + window_chars > self.max_chapter_chars
            )
            if current and (boundary_score >= 0.68 or forced_boundary):
                chapters.append(
                    self._build_chapter(
                        len(chapters),
                        current,
                        boundary_score if not forced_boundary else 1.0,
                        "MAX_CHAPTER_SIZE" if forced_boundary else "SEMANTIC_AND_VISUAL",
                    )
                )
                current = []
                current_chars = 0
            current.append(window | {"classification": classification, "boundary_score": boundary_score})
            current_chars += window_chars
            last_domain = classification["primary_domain"]

        if current:
            chapters.append(self._build_chapter(len(chapters), current, 0.5, "END_OF_VIDEO"))
        return self._merge_short_chapters(chapters)

    def _build_chapter(self, index: int, windows: list[dict], boundary_score: float, boundary_source: str) -> dict:
        start_ms = int(windows[0].get("start_ms") or 0)
        end_ms = int(windows[-1].get("end_ms") or start_ms)
        text = " ".join(str(window.get("transcript_text") or "") for window in windows).strip()
        ocr_text = " ".join(str(window.get("ocr_text") or "") for window in windows).strip()
        visual_summary = " ".join(str(window.get("visual_summary") or "") for window in windows).strip()
        classification = self.classifier.classify(text, ocr_text, visual_summary)
        window_entities = [entity for window in windows for entity in (window.get("entities") or [])]
        # 帧是口播的视觉增强：实体以口播为锚、以 LLM 视觉判定为辅。
        # 口播提到的排最前；visual_summary / 帧 symbols 由多模态模型按画面与口播的相关性甄别后采纳；
        # 原始 OCR 不再直接提供实体——其中行情软件侧边栏公告等内容与口播无关。
        spoken_entities = self.classifier.extract_entities(text)
        visual_entities = self.classifier.extract_entities(visual_summary)
        entities: list[str] = []
        for source in (spoken_entities, visual_entities, window_entities):
            for entity in source:
                if entity not in entities:
                    entities.append(entity)
        entities = entities[:12]
        # 标题优先用口播实体；其次人类可读实体；纯代码（如 999999）只在无可读实体时使用
        bare_code = r"(?i)\d{6}|\d{4}\.HK"
        title_entity = (
            next((entity for entity in spoken_entities if entity in entities), None)
            or next((entity for entity in entities if not re.fullmatch(bare_code, entity)), None)
            or (entities[0] if entities else None)
        )
        title = self.classifier.infer_title(text, classification["primary_domain"], [title_entity] if title_entity else [])
        summary = self._summarize_text(text, ocr_text, visual_summary)
        return {
            "chapter_index": index,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "title": title,
            "chapter_type": classification["chapter_type"],
            "primary_domain": classification["primary_domain"],
            "secondary_domains": classification["secondary_domains"],
            "summary": summary,
            "entities": entities[:12],
            "boundary_source": boundary_source,
            "boundary_score": round(boundary_score, 4),
            "confidence_score": classification["confidence_score"],
            "windows": windows,
            "content_hash": hashlib.sha256(f"{start_ms}:{end_ms}:{text}:{ocr_text}".encode("utf-8")).hexdigest(),
            "parser_version": "v3.0-rule",
        }

    def _merge_short_chapters(self, chapters: list[dict]) -> list[dict]:
        if len(chapters) <= 1:
            return chapters
        merged: list[dict] = []
        for chapter in chapters:
            duration = int(chapter["end_ms"]) - int(chapter["start_ms"])
            if merged and duration < self.min_chapter_ms and chapter["chapter_type"] not in {"ADVERTISEMENT", "RISK_WARNING"}:
                previous = merged[-1]
                previous["end_ms"] = chapter["end_ms"]
                previous["windows"].extend(chapter.get("windows") or [])
                previous["summary"] = self._summarize_text(
                    " ".join(str(window.get("transcript_text") or "") for window in previous["windows"]),
                    " ".join(str(window.get("ocr_text") or "") for window in previous["windows"]),
                    "",
                )
                continue
            merged.append(chapter)
        for index, chapter in enumerate(merged):
            chapter["chapter_index"] = index
        return merged

    @staticmethod
    def _boundary_score(previous: dict | None, current: dict, previous_domain: str | None, current_domain: str) -> float:
        if previous is None:
            return 0.0
        text = re.sub(r"\s+", "", str(current.get("transcript_text") or ""))
        discourse = 1.0 if any(marker in text for marker in ("接下来", "下面", "再看", "然后看", "最后", "总结一下")) else 0.0
        domain_shift = 1.0 if previous_domain and previous_domain != current_domain else 0.0
        prev_entities = set(previous.get("entities") or [])
        curr_entities = set(current.get("entities") or [])
        entity_shift = 1.0 if curr_entities and prev_entities and not (curr_entities & prev_entities) else 0.0
        visual_shift = 1.0 if bool(previous.get("visual_summary")) != bool(current.get("visual_summary")) else 0.0
        pause = 0.0
        if int(current.get("start_ms") or 0) - int(previous.get("end_ms") or 0) > 2500:
            pause = 1.0
        return 0.25 * discourse + 0.25 * domain_shift + 0.2 * entity_shift + 0.15 * visual_shift + 0.15 * pause

    @staticmethod
    def _summarize_text(text: str, ocr_text: str, visual_summary: str, limit: int = 180) -> str:
        merged = " ".join(part for part in (text, ocr_text, visual_summary) if part).strip()
        merged = re.sub(r"\s+", " ", merged)
        return merged[:limit]

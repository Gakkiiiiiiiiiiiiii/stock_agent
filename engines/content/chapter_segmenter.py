from __future__ import annotations

import hashlib
import re

from engines.content.chapter_classifier import ChapterClassifier


class ChapterSegmenter:
    def __init__(self, classifier: ChapterClassifier | None = None, min_chapter_seconds: int = 30) -> None:
        self.classifier = classifier or ChapterClassifier()
        self.min_chapter_ms = min_chapter_seconds * 1000

    def segment(self, windows: list[dict]) -> list[dict]:
        if not windows:
            return []
        chapters: list[dict] = []
        current: list[dict] = []
        last_domain: str | None = None

        for window in windows:
            classification = self.classifier.classify(window.get("transcript_text", ""), window.get("ocr_text", ""), window.get("visual_summary", ""))
            boundary_score = self._boundary_score(current[-1] if current else None, window, last_domain, classification["primary_domain"])
            if current and boundary_score >= 0.68:
                chapters.append(self._build_chapter(len(chapters), current, boundary_score, "SEMANTIC_AND_VISUAL"))
                current = []
            current.append(window | {"classification": classification, "boundary_score": boundary_score})
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
        entities = sorted({entity for window in windows for entity in (window.get("entities") or [])})
        entities.extend(item for item in self.classifier.extract_entities(f"{text} {ocr_text}") if item not in entities)
        title = self.classifier.infer_title(text, classification["primary_domain"], entities)
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

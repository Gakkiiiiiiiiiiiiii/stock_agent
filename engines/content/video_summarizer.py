from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.model_providers import AnalysisModelClient


logger = logging.getLogger(__name__)


class VideoSummarizer:
    def __init__(self, model_client: AnalysisModelClient | None = None) -> None:
        self.model_client = model_client or AnalysisModelClient()

    def summarize(
        self,
        metadata: dict,
        transcript: dict,
        mode: str = "investment",
        visual_context: dict | None = None,
        chunks: list[dict] | None = None,
        events: list[dict] | None = None,
        video_type: str | None = None,
    ) -> dict:
        text = (transcript.get("text") or "").strip()
        if not text and transcript.get("segments"):
            text = "\n".join(str(segment.get("text") or "").strip() for segment in transcript["segments"]).strip()
            transcript = transcript | {"text": text}
        if not text:
            raise ValueError("transcript text is empty")
        if self.model_client.available():
            try:
                prompt = self._build_prompt(
                    metadata=metadata,
                    transcript=transcript,
                    mode=mode,
                    visual_context=visual_context,
                    chunks=chunks or [],
                    events=events or [],
                    video_type=video_type,
                )
                response = self.model_client.complete(
                    prompt=prompt,
                    system=(
                        "You summarize Bilibili financial videos into structured JSON. "
                        "Prefer structured financial events over raw transcript. "
                        "Keep facts, opinions, forecasts, conditions, and invalidations separate. "
                        "Return valid JSON only."
                    ),
                    temperature=0.2,
                )
                content = (response.get("content") or "").strip()
                payload = self._parse_json(content)
                return self._normalize_summary(
                    payload,
                    metadata=metadata,
                    transcript=transcript,
                    mode=mode,
                    visual_context=visual_context,
                    events=events or [],
                    chunks=chunks or [],
                    video_type=video_type,
                ) | {
                    "llm_provider": response.get("provider"),
                    "llm_model": response.get("model"),
                    "degraded": False,
                }
            except Exception:
                logger.warning("LLM 视频总结失败，降级为结构化规则摘要", exc_info=True)
        return self._fallback_summary(
            metadata=metadata,
            transcript=transcript,
            mode=mode,
            visual_context=visual_context,
            chunks=chunks or [],
            events=events or [],
            video_type=video_type,
        )

    def _build_prompt(
        self,
        metadata: dict,
        transcript: dict,
        mode: str,
        visual_context: dict | None = None,
        chunks: list[dict] | None = None,
        events: list[dict] | None = None,
        video_type: str | None = None,
    ) -> str:
        prompt = (
            "请基于以下结构化金融视频解析结果，输出结构化 JSON，总结其投资观点。\n"
            "必须包含字段：core_summary, bull_points, bear_points, themes, symbols, catalysts, risks, actionable_view, "
            "evidence_segments, confidence_score, fact_points, forecast_points, invalidation_conditions。\n"
            "要求：\n"
            "- 优先依据 financial_events，总结时不要把观点改写成事实。\n"
            "- 保留条件语句、证伪条件、时间范围、关键点位与风险。\n"
            "- 如果事件之间有冲突，优先采纳 conflict_status=active 或较新的观点，同时说明更新关系。\n"
            "- 只有口播或字幕明确提及的点位，才能写入核心结论。\n"
            f"summary_mode: {mode}\n"
            f"video_type: {video_type or 'GENERAL_FINANCE'}\n"
            f"video_title: {metadata.get('title', '')}\n"
            f"video_author: {metadata.get('author_name', '')}\n"
        )
        event_outline = self._prepare_event_outline(events or [])
        chunk_outline = self._prepare_chunk_outline(chunks or [])
        if event_outline:
            prompt += f"\nfinancial_events:\n{event_outline}"
        if chunk_outline:
            prompt += f"\n\nchapter_outline:\n{chunk_outline}"
        if not event_outline:
            prompt += f"\n\ntranscript_outline:\n{self._prepare_transcript_outline(transcript)}"
        visual_outline = self._prepare_visual_outline(visual_context)
        if visual_outline:
            prompt += f"\n\nvisual_context_outline:\n{visual_outline}"
        return prompt

    def _fallback_summary(
        self,
        metadata: dict,
        transcript: dict,
        mode: str,
        visual_context: dict | None = None,
        chunks: list[dict] | None = None,
        events: list[dict] | None = None,
        video_type: str | None = None,
    ) -> dict:
        evidence = []
        active_events = [event for event in events or [] if event.get("conflict_status") != "superseded"]
        for event in active_events[:4]:
            evidence.append(
                {
                    "start_ms": event.get("start_ms"),
                    "end_ms": event.get("end_ms"),
                    "text": event.get("statement", ""),
                }
            )
        if not evidence:
            for segment in transcript.get("segments", [])[:3]:
                evidence.append(
                    {
                        "start_ms": segment.get("start_ms"),
                        "end_ms": segment.get("end_ms"),
                        "text": segment.get("text", ""),
                    }
                )
        evidence.extend((visual_context or {}).get("evidence_segments") or [])
        snippet = self._build_fallback_core_summary(events=active_events, chunks=chunks or [], transcript=transcript)
        visual_outline = self._prepare_visual_outline(visual_context)
        themes = self._collect_themes_from_events(active_events)
        symbols = self._collect_symbols_from_events(active_events)
        invalidations = [str(event.get("invalidation_text") or "").strip() for event in active_events if str(event.get("invalidation_text") or "").strip()]
        forecast_points = [event.get("statement") for event in active_events if event.get("claim_type") == "FORECAST"]
        fact_points = [event.get("statement") for event in active_events if event.get("claim_type") == "FACT"]
        return {
            "summary_mode": mode,
            "summary_markdown": f"# {metadata.get('title', 'Video Summary')}\n\n{snippet}\n\n{visual_outline}".strip(),
            "core_summary": snippet if not visual_outline else f"{snippet}\n\n画面补充：{visual_outline[:300]}",
            "bull_points": [event.get("statement") for event in active_events if event.get("sentiment") == "BULLISH"][:5],
            "bear_points": [event.get("statement") for event in active_events if event.get("sentiment") == "BEARISH"][:5],
            "themes": themes,
            "symbols": symbols,
            "catalysts": [event.get("statement") for event in active_events if event.get("event_type") == "CATALYST"][:4],
            "risks": [event.get("statement") for event in active_events if event.get("event_type") == "RISK"][:4] or ["模型未配置，当前为结构化规则摘要"],
            "actionable_view": self._fallback_actionable_view(active_events),
            "evidence_segments": evidence,
            "confidence_score": 0.35,
            "fact_points": fact_points[:4],
            "forecast_points": forecast_points[:4],
            "invalidation_conditions": invalidations[:4],
            "video_type": video_type or "GENERAL_FINANCE",
            "llm_provider": "fallback",
            "llm_model": "none",
            "degraded": True,
        }

    def _normalize_summary(
        self,
        payload: dict[str, Any],
        metadata: dict,
        transcript: dict,
        mode: str,
        visual_context: dict | None = None,
        events: list[dict] | None = None,
        chunks: list[dict] | None = None,
        video_type: str | None = None,
    ) -> dict:
        evidence = payload.get("evidence_segments") or []
        if not evidence:
            for event in (events or [])[:4]:
                evidence.append(
                    {
                        "start_ms": event.get("start_ms"),
                        "end_ms": event.get("end_ms"),
                        "text": event.get("statement", ""),
                    }
                )
        if not evidence:
            for segment in transcript.get("segments", [])[:3]:
                evidence.append(
                    {
                        "start_ms": segment.get("start_ms"),
                        "end_ms": segment.get("end_ms"),
                        "text": segment.get("text", ""),
                    }
                )
        for item in (visual_context or {}).get("evidence_segments") or []:
            if len(evidence) >= 8:
                break
            evidence.append(item)
        core_summary = str(payload.get("core_summary") or "").strip()
        if not core_summary:
            core_summary = transcript.get("text", "")[:400]
        summary_markdown = payload.get("summary_markdown")
        if not summary_markdown:
            summary_markdown = f"# {metadata.get('title', 'Video Summary')}\n\n{core_summary}"
        themes = self._ensure_string_list(payload.get("themes"))
        if not themes and events:
            themes = self._collect_themes_from_events(events)
        symbols = self._ensure_string_list(payload.get("symbols"))
        if not symbols and events:
            symbols = self._collect_symbols_from_events(events)
        core_summary = self._apply_symbol_aliases(core_summary, symbols)
        bull_points = self._apply_symbol_aliases_to_list(self._ensure_string_list(payload.get("bull_points")), symbols)
        bear_points = self._apply_symbol_aliases_to_list(self._ensure_string_list(payload.get("bear_points")), symbols)
        catalysts = self._apply_symbol_aliases_to_list(self._ensure_string_list(payload.get("catalysts")), symbols)
        risks = self._apply_symbol_aliases_to_list(self._ensure_string_list(payload.get("risks")), symbols)
        fact_points = self._apply_symbol_aliases_to_list(self._ensure_string_list(payload.get("fact_points")), symbols)
        forecast_points = self._apply_symbol_aliases_to_list(self._ensure_string_list(payload.get("forecast_points")), symbols)
        invalidation_conditions = self._apply_symbol_aliases_to_list(
            self._ensure_string_list(payload.get("invalidation_conditions")),
            symbols,
        )
        selected_chunks = self._select_representative_chunks(chunks or [], limit=6)
        return {
            "summary_mode": mode,
            "summary_markdown": summary_markdown,
            "core_summary": core_summary,
            "bull_points": bull_points,
            "bear_points": bear_points,
            "themes": themes,
            "symbols": symbols,
            "catalysts": catalysts,
            "risks": risks,
            "actionable_view": str(payload.get("actionable_view") or "").strip(),
            "evidence_segments": evidence,
            "confidence_score": self._coerce_confidence_score(payload.get("confidence_score")),
            "fact_points": fact_points,
            "forecast_points": forecast_points,
            "invalidation_conditions": invalidation_conditions,
            "video_type": video_type or payload.get("video_type") or "GENERAL_FINANCE",
            "chapter_summaries": payload.get("chapter_summaries") if isinstance(payload.get("chapter_summaries"), list) else [
                self._render_chunk_chapter(chunk) for chunk in selected_chunks
            ],
        }

    @staticmethod
    def _prepare_visual_outline(visual_context: dict | None) -> str:
        if not visual_context:
            return ""
        return str(visual_context.get("outline") or "").strip()[:6000]

    def _prepare_transcript_outline(self, transcript: dict) -> str:
        segments = transcript.get("segments") or []
        if not segments:
            return (transcript.get("text") or "")[:16000]
        chunks = self._build_segment_chunks(segments, target_chars=2800, max_chunks=6)
        if len(chunks) == 1:
            return chunks[0]["text"][:16000]
        if not self.model_client.available():
            return "\n\n".join(
                f"[片段摘要 {index + 1}]\n{chunk['text'][:2200]}"
                for index, chunk in enumerate(chunks)
            )[:16000]
        chunk_summaries: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            response = self.model_client.complete(
                prompt=(
                    "请把下面的视频转写片段提炼成简洁中文摘要，重点保留："
                    "市场判断、板块轮动、交易节奏、风险提示、提到的主题和个股。"
                    "如果没有明确提到个股或行业，请直接写未明确提及。\n"
                    f"片段 {index} 时间范围: {chunk['start_ms']} - {chunk['end_ms']} ms\n"
                    f"内容:\n{chunk['text']}"
                ),
                system="你是投研助理，请压缩长语音转写，但不要杜撰。",
                temperature=0.2,
            )
            content = (response.get("content") or "").strip()
            chunk_summaries.append(
                f"[片段摘要 {index} | {chunk['start_ms']} - {chunk['end_ms']} ms]\n{content or chunk['text'][:600]}"
            )
        return "\n\n".join(chunk_summaries)[:16000]

    @staticmethod
    def _prepare_event_outline(events: list[dict]) -> str:
        if not events:
            return ""
        lines = []
        for event in events[:18]:
            entities = ", ".join(
                VideoSummarizer._format_entity_label(item)
                for item in event.get("entities") or []
                if isinstance(item, dict) and VideoSummarizer._format_entity_label(item)
            )
            lines.append(
                "\n".join(
                    [
                        f"[事件 {event.get('event_index', '?')} | {event.get('start_ms', 0)}-{event.get('end_ms', 0)} ms]",
                        f"类型：{event.get('event_type')} / {event.get('claim_type')} / {event.get('sentiment')}",
                        f"陈述：{event.get('statement')}",
                        f"实体：{entities or '未识别'}",
                        f"条件：{event.get('condition_text') or '无'}",
                        f"证伪：{event.get('invalidation_text') or '无'}",
                        f"冲突状态：{event.get('conflict_status') or 'unknown'}",
                    ]
                )
            )
        return "\n\n".join(lines)

    @staticmethod
    def _prepare_chunk_outline(chunks: list[dict]) -> str:
        if not chunks:
            return ""
        selected_chunks = VideoSummarizer._select_representative_chunks(chunks, limit=8)
        return "\n\n".join(VideoSummarizer._render_chunk_chapter(chunk) for chunk in selected_chunks)

    @staticmethod
    def _render_chunk_chapter(chunk: dict) -> str:
        return "\n".join(
            [
                f"[章节 {chunk.get('chunk_index', 0) + 1} | {chunk.get('start_ms', 0)}-{chunk.get('end_ms', 0)} ms]",
                f"主题：{chunk.get('topic') or '未分类'}",
                f"口播：{str(chunk.get('transcript_text') or '')[:260]}",
                f"OCR：{str(chunk.get('ocr_text') or '')[:180] or '无'}",
                f"视觉：{str(chunk.get('visual_focus') or '')[:180] or '无'}",
            ]
        )

    @staticmethod
    def _select_representative_chunks(chunks: list[dict], limit: int = 8) -> list[dict]:
        if len(chunks) <= limit:
            return chunks
        head_count = min(4, limit)
        selected = list(chunks[:head_count])
        selected_keys = {VideoSummarizer._chunk_key(chunk) for chunk in selected}
        ranked_tail = sorted(
            (chunk for chunk in chunks[head_count:] if VideoSummarizer._chunk_key(chunk) not in selected_keys),
            key=VideoSummarizer._score_chunk,
            reverse=True,
        )
        for chunk in ranked_tail:
            if len(selected) >= limit:
                break
            selected.append(chunk)
            selected_keys.add(VideoSummarizer._chunk_key(chunk))
        return sorted(selected, key=lambda item: (int(item.get("start_ms") or 0), int(item.get("chunk_index") or 0)))

    @staticmethod
    def _chunk_key(chunk: dict) -> tuple[int, int]:
        return (int(chunk.get("chunk_index") or 0), int(chunk.get("start_ms") or 0))

    @staticmethod
    def _score_chunk(chunk: dict) -> float:
        transcript_text = str(chunk.get("transcript_text") or "")
        ocr_text = str(chunk.get("ocr_text") or "")
        topic = str(chunk.get("topic") or "")
        visual_focus = str(chunk.get("visual_focus") or "")
        merged = " ".join([topic, transcript_text, ocr_text, visual_focus])
        score = 0.0
        if ocr_text:
            score += 3.0
        if visual_focus:
            score += 1.0
        if re.search(r"\b\d{6}\b", merged):
            score += 5.0
        if re.search(r"[\u4e00-\u9fff]{2,8}", topic) and topic not in {"未分类", "GENERAL_FINANCE"}:
            score += 1.0
        for token in ("买点", "洗盘", "压力", "突破", "反弹", "风险", "止跌"):
            if token in merged:
                score += 1.5
        score += min(int(chunk.get("start_ms") or 0) / 1_000_000.0, 2.0)
        return score

    @staticmethod
    def _format_entity_label(entity: dict) -> str:
        name = str(entity.get("name") or "").strip()
        ticker = str(entity.get("ticker") or "").strip()
        if name and ticker and name != ticker:
            return f"{name} ({ticker})"
        return ticker or name

    @staticmethod
    def _apply_symbol_aliases(text: str, symbols: list[str]) -> str:
        result = str(text or "")
        for symbol in symbols:
            match = re.match(r"(.+?) \(([^()]+)\)$", str(symbol).strip())
            if not match:
                continue
            name = match.group(1).strip()
            ticker = match.group(2).strip()
            bare_ticker = ticker.split(".")[0]
            if bare_ticker and bare_ticker in result and name not in result:
                result = re.sub(
                    rf"(?<![0-9A-Za-z]){re.escape(bare_ticker)}(?:\.(?:SH|SZ))?(?![0-9A-Za-z])",
                    f"{name} ({ticker})",
                    result,
                )
        return result

    @staticmethod
    def _apply_symbol_aliases_to_list(items: list[str], symbols: list[str]) -> list[str]:
        return [VideoSummarizer._apply_symbol_aliases(item, symbols) for item in items]

    @staticmethod
    def _collect_themes_from_events(events: list[dict]) -> list[str]:
        themes: list[str] = []
        for event in events:
            for entity in event.get("entities") or []:
                if not isinstance(entity, dict):
                    continue
                entity_type = str(entity.get("entity_type") or "")
                name = str(entity.get("name") or entity.get("ticker") or "").strip()
                if entity_type in {"THEME", "INDUSTRY", "MACRO"} and name and name not in themes:
                    themes.append(name)
        return themes[:8]

    @staticmethod
    def _collect_symbols_from_events(events: list[dict]) -> list[str]:
        symbols: list[str] = []
        for event in events:
            for entity in event.get("entities") or []:
                if not isinstance(entity, dict):
                    continue
                entity_type = str(entity.get("entity_type") or "")
                label = VideoSummarizer._format_entity_label(entity)
                if label and entity_type in {"EQUITY", "INDEX", "COMMODITY"} and label not in symbols:
                    symbols.append(label)
        return symbols[:8]

    @staticmethod
    def _build_fallback_core_summary(events: list[dict], chunks: list[dict], transcript: dict) -> str:
        if events:
            active = [event for event in events if event.get("conflict_status") != "superseded"]
            snippets = [str(event.get("statement") or "").strip() for event in active[:4] if str(event.get("statement") or "").strip()]
            if snippets:
                return "；".join(snippets)
        if chunks:
            chunk_snippets = [str(chunk.get("transcript_text") or "").strip()[:120] for chunk in chunks[:3] if str(chunk.get("transcript_text") or "").strip()]
            if chunk_snippets:
                return "；".join(chunk_snippets)
        return transcript.get("text", "")[:400]

    @staticmethod
    def _fallback_actionable_view(events: list[dict]) -> str:
        action_events = [event for event in events if event.get("event_type") == "TRADING_ACTION"]
        if action_events:
            return str(action_events[-1].get("statement") or "").strip() or "需要人工复核"
        if any(event.get("sentiment") == "BEARISH" for event in events):
            return "当前更适合谨慎观察，等待条件确认。"
        if any(event.get("sentiment") == "BULLISH" for event in events):
            return "当前偏正面，但仍需结合条件和风险控制。"
        return "需要人工复核"

    @staticmethod
    def _build_segment_chunks(segments: list[dict], target_chars: int = 2800, max_chunks: int = 6) -> list[dict]:
        raw_chunks: list[dict] = []
        current_texts: list[str] = []
        current_start: int | None = None
        current_end: int | None = None
        current_length = 0

        for segment in segments:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            text_length = len(text)
            if current_texts and current_length + text_length > target_chars:
                raw_chunks.append(
                    {
                        "start_ms": current_start or 0,
                        "end_ms": current_end or 0,
                        "text": "\n".join(current_texts),
                    }
                )
                current_texts = []
                current_start = None
                current_end = None
                current_length = 0
            if current_start is None:
                current_start = int(segment.get("start_ms") or 0)
            current_end = int(segment.get("end_ms") or 0)
            current_texts.append(text)
            current_length += text_length

        if current_texts:
            raw_chunks.append(
                {
                    "start_ms": current_start or 0,
                    "end_ms": current_end or 0,
                    "text": "\n".join(current_texts),
                }
            )
        if not raw_chunks:
            return [{"start_ms": 0, "end_ms": 0, "text": ""}]
        if len(raw_chunks) <= max_chunks:
            return raw_chunks

        # Merge neighboring raw chunks so the final outline covers the full timeline
        # instead of truncating to only the first few chunks of a long video.
        merged: list[dict] = []
        group_size = max(1, (len(raw_chunks) + max_chunks - 1) // max_chunks)
        for start in range(0, len(raw_chunks), group_size):
            group = raw_chunks[start : start + group_size]
            merged.append(
                {
                    "start_ms": group[0]["start_ms"],
                    "end_ms": group[-1]["end_ms"],
                    "text": "\n".join(item["text"] for item in group if item["text"]),
                }
            )
        return merged[:max_chunks]

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
        return json.loads(text)

    @staticmethod
    def _ensure_string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _coerce_confidence_score(value: Any) -> float:
        if value is None:
            return 0.5
        if isinstance(value, (int, float)):
            score = float(value)
            return max(0.0, min(score, 1.0))
        text = str(value).strip()
        if not text:
            return 0.5
        try:
            score = float(text)
            return max(0.0, min(score, 1.0))
        except ValueError:
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
            if match:
                score = float(match.group(1))
                if score > 1:
                    score = score / 100.0 if score <= 100 else 1.0
                return max(0.0, min(score, 1.0))
        return 0.5

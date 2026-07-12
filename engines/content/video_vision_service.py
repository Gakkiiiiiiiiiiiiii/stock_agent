from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from app.model_providers import VisualModelClient
from engines.content.video_ocr_service import VideoOcrService


class VideoVisionService:
    def __init__(
        self,
        model_client: VisualModelClient | None = None,
        ocr_service: VideoOcrService | None = None,
        max_frames_per_run: int | None = None,
    ) -> None:
        self.model_client = model_client or VisualModelClient()
        self.ocr_service = ocr_service or VideoOcrService()
        self.max_frames_per_run = int(os.getenv("VIDEO_VISION_MAX_FRAMES", str(max_frames_per_run or 12)))

    def available(self) -> bool:
        return self.model_client.available() or self.ocr_service.available()

    def analyze_frames(self, metadata: dict, transcript: dict, frames: list[dict]) -> list[dict]:
        if not frames:
            return []
        insights: list[dict] = []
        for frame in frames[: self.max_frames_per_run]:
            insight = dict(frame)
            ocr_text = self._safe_extract_ocr(frame["image_path"])
            related_text = self._collect_nearby_transcript(transcript.get("segments") or [], int(frame.get("timestamp_ms") or 0))
            visual_payload = self._safe_visual_analyze(
                metadata=metadata,
                image_path=frame["image_path"],
                timestamp_ms=int(frame.get("timestamp_ms") or 0),
                ocr_text=ocr_text,
                related_text=related_text,
            )
            insight["ocr_text"] = ocr_text
            insight["related_text"] = related_text
            insight["visual_summary"] = str(visual_payload.get("visual_summary") or "").strip()
            insight["themes"] = self._ensure_string_list(visual_payload.get("themes"))
            insight["symbols"] = self._ensure_string_list(visual_payload.get("symbols"))
            insight["visual_tags"] = self._ensure_string_list(visual_payload.get("visual_tags"))
            insight["objects"] = visual_payload.get("objects") if isinstance(visual_payload.get("objects"), list) else []
            insight["confidence_score"] = self._coerce_confidence(visual_payload.get("confidence_score"))
            if insight["ocr_text"] or insight["visual_summary"]:
                insights.append(insight)
        return insights

    def _safe_extract_ocr(self, image_path: str) -> str:
        try:
            return self.ocr_service.extract_text(image_path)
        except Exception:
            return ""

    def _safe_visual_analyze(self, metadata: dict, image_path: str, timestamp_ms: int, ocr_text: str, related_text: str) -> dict[str, Any]:
        if not self.model_client.available():
            return {
                "visual_summary": "",
                "themes": [],
                "symbols": [],
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.0 if not ocr_text else 0.3,
            }
        try:
            response = self.model_client.create_chat_completion(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": self._build_prompt(
                                    metadata=metadata,
                                    timestamp_ms=timestamp_ms,
                                    ocr_text=ocr_text,
                                    related_text=related_text,
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": self._build_data_url(Path(image_path)),
                                },
                            },
                        ],
                    }
                ],
                system=(
                    "你是投研视频视觉解析器。请识别画面中的文字、图表、K线、表格和结论。"
                    "只返回 JSON，不要输出多余解释。不要杜撰看不清的内容。"
                ),
                temperature=0.1,
                max_tokens=700,
            )
        except Exception:
            return {
                "visual_summary": "",
                "themes": [],
                "symbols": [],
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.0 if not ocr_text else 0.3,
            }
        content = (((response.get("choices") or [{}])[0]).get("message") or {}).get("content", "")
        if isinstance(content, list):
            content = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        try:
            return self._parse_json(str(content or ""))
        except Exception:
            return {
                "visual_summary": str(content or "").strip(),
                "themes": [],
                "symbols": [],
                "confidence_score": 0.4 if content else 0.0,
            }

    @staticmethod
    def _build_prompt(metadata: dict, timestamp_ms: int, ocr_text: str, related_text: str) -> str:
        return (
            "请结合图片和已识别 OCR 文本，提取这一帧是否包含值得纳入投资总结的信息。\n"
            "输出 JSON，字段必须包含：visual_summary, themes, symbols, visual_tags, objects, confidence_score。\n"
            "要求：\n"
            "- visual_summary: 用简洁中文描述这张图表达的核心信息。\n"
            "- themes: 画面里明确支持的主题列表。\n"
            "- symbols: 画面里明确出现或强相关的股票代码列表。\n"
            "- visual_tags: 从 candlestick_chart, line_chart, financial_table, presentation_slide, news_page, subtitle 中选择。\n"
            "- objects: 若能明确识别出股票代码、价格、指标，输出对象数组。\n"
            "- confidence_score: 0 到 1。\n"
            "不要根据口播臆造图中没有的信息。\n"
            f"video_title: {metadata.get('title', '')}\n"
            f"timestamp_ms: {timestamp_ms}\n"
            f"ocr_text: {ocr_text or '未识别到文字'}\n"
            f"nearby_transcript: {related_text or '未提供相关口播'}"
        )

    @staticmethod
    def _build_data_url(image_path: Path) -> str:
        suffix = image_path.suffix.lower().lstrip(".") or "jpeg"
        mime_type = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _collect_nearby_transcript(segments: list[dict], timestamp_ms: int, window_ms: int = 12000) -> str:
        parts = []
        for segment in segments:
            start_ms = int(segment.get("start_ms") or 0)
            end_ms = int(segment.get("end_ms") or start_ms)
            if start_ms - window_ms <= timestamp_ms <= end_ms + window_ms:
                text = str(segment.get("text") or "").strip()
                if text:
                    parts.append(text)
        return " ".join(parts[:4]).strip()

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
    def _coerce_confidence(value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(score, 1.0))

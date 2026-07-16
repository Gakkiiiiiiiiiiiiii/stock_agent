from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx

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
        self._image_input_supported: bool | None = None
        self.text_fallback_enabled = os.getenv("VIDEO_VISION_TEXT_FALLBACK", "1").strip().lower() not in {"0", "false", "no"}

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
            if ocr_text and self.text_fallback_enabled and not self._has_meaningful_visual_payload(visual_payload):
                visual_payload = self._merge_visual_payloads(
                    primary=visual_payload,
                    secondary=self._safe_textual_visual_analyze(
                        metadata=metadata,
                        timestamp_ms=int(frame.get("timestamp_ms") or 0),
                        ocr_text=ocr_text,
                        related_text=related_text,
                    ),
                )
            insight["ocr_text"] = ocr_text
            insight["related_text"] = related_text
            insight["visual_summary"] = str(visual_payload.get("visual_summary") or "").strip()
            insight["themes"] = self._ensure_string_list(visual_payload.get("themes"))
            insight["symbols"] = self._ensure_string_list(visual_payload.get("symbols"))
            insight["visual_tags"] = self._ensure_string_list(visual_payload.get("visual_tags"))
            insight["objects"] = visual_payload.get("objects") if isinstance(visual_payload.get("objects"), list) else []
            insight["confidence_score"] = self._coerce_confidence(visual_payload.get("confidence_score"))
            if not insight["visual_summary"]:
                insight["visual_summary"] = self._build_fallback_visual_summary(
                    related_text=related_text,
                    ocr_text=ocr_text,
                    trigger_source=str(frame.get("trigger_source") or ""),
                    error_message=str(visual_payload.get("error_message") or "").strip(),
                )
                if insight["confidence_score"] <= 0:
                    insight["confidence_score"] = 0.18 if related_text else 0.12
            if insight["ocr_text"] or insight["visual_summary"] or related_text:
                insights.append(insight)
        return insights

    def _safe_extract_ocr(self, image_path: str) -> str:
        try:
            return self.ocr_service.extract_text(image_path)
        except Exception:
            return ""

    def _safe_visual_analyze(self, metadata: dict, image_path: str, timestamp_ms: int, ocr_text: str, related_text: str) -> dict[str, Any]:
        if self._image_input_supported is False:
            return {
                "visual_summary": "",
                "themes": [],
                "symbols": [],
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.0 if not ocr_text else 0.3,
                "error_message": "configured visual model does not accept image input",
            }
        if not self.model_client.available():
            return {
                "visual_summary": "",
                "themes": [],
                "symbols": [],
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.0 if not ocr_text else 0.3,
                "error_message": "visual model not configured",
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
            self._image_input_supported = True
        except httpx.HTTPStatusError as exc:
            error_body = self._extract_http_error_message(exc)
            if exc.response is not None and exc.response.status_code == 400 and self._looks_like_unsupported_image_input(error_body):
                self._image_input_supported = False
            return {
                "visual_summary": "",
                "themes": [],
                "symbols": [],
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.0 if not ocr_text else 0.3,
                "error_message": error_body or str(exc),
            }
        except Exception as exc:
            return {
                "visual_summary": "",
                "themes": [],
                "symbols": [],
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.0 if not ocr_text else 0.3,
                "error_message": str(exc),
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
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.4 if content else 0.0,
                "error_message": "visual model returned non-json content",
            }

    def _safe_textual_visual_analyze(
        self,
        metadata: dict,
        timestamp_ms: int,
        ocr_text: str,
        related_text: str,
    ) -> dict[str, Any]:
        if not ocr_text or not self.model_client.available():
            return {
                "visual_summary": "",
                "themes": [],
                "symbols": [],
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.0,
                "error_message": "ocr-guided visual fallback unavailable",
            }
        try:
            response = self.model_client.complete(
                prompt=self._build_textual_fallback_prompt(
                    metadata=metadata,
                    timestamp_ms=timestamp_ms,
                    ocr_text=ocr_text,
                    related_text=related_text,
                ),
                system=(
                    "你是投研视频关键帧复核器。你当前看不到原图，只能依据 OCR 文本与附近口播做谨慎复核。"
                    "只提取 OCR 明确支持、或可由 OCR 与口播共同印证的信息。"
                    "输出合法 JSON，不要附加解释。"
                ),
                temperature=0.1,
            )
        except Exception as exc:
            return {
                "visual_summary": "",
                "themes": [],
                "symbols": [],
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.0,
                "error_message": str(exc),
            }
        content = str(response.get("content") or "").strip()
        try:
            payload = self._parse_json(content)
        except Exception:
            return {
                "visual_summary": content,
                "themes": [],
                "symbols": [],
                "visual_tags": [],
                "objects": [],
                "confidence_score": 0.38 if content else 0.0,
                "error_message": "ocr-guided fallback returned non-json content",
            }
        if not payload.get("confidence_score"):
            payload["confidence_score"] = 0.42 if related_text else 0.36
        return payload

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
    def _build_textual_fallback_prompt(metadata: dict, timestamp_ms: int, ocr_text: str, related_text: str) -> str:
        return (
            "当前模型无法直接读取图片。请仅根据关键帧 OCR 文本和附近口播，对这一帧进行谨慎复核。\n"
            "输出 JSON，字段必须包含：visual_summary, themes, symbols, visual_tags, objects, confidence_score。\n"
            "要求：\n"
            "- visual_summary: 用简洁中文描述 OCR 明确支持的画面信息；若只能大致判断，要写清楚“疑似/大概率”。\n"
            "- themes: 仅输出 OCR 或口播共同支持的主题。\n"
            "- symbols: 仅输出 OCR 明确识别出的代码、简称或指数。\n"
            "- visual_tags: 从 candlestick_chart, line_chart, financial_table, presentation_slide, news_page, subtitle 中选择。\n"
            "- objects: 若 OCR 明确出现板块、股票代码、指标、价格、涨跌幅，可输出对象数组。\n"
            "- confidence_score: 0 到 1，OCR 证据弱时不要给高分。\n"
            "- 不要把口播单独提到但 OCR 没支持的信息写成画面事实。\n"
            f"video_title: {metadata.get('title', '')}\n"
            f"timestamp_ms: {timestamp_ms}\n"
            f"ocr_text: {ocr_text}\n"
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

    @staticmethod
    def _has_meaningful_visual_payload(payload: dict[str, Any]) -> bool:
        if str(payload.get("visual_summary") or "").strip():
            return True
        for key in ("themes", "symbols", "visual_tags", "objects"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                return True
        return False

    @staticmethod
    def _merge_visual_payloads(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
        if not secondary:
            return primary
        merged = dict(primary)
        for key in ("visual_summary", "error_message"):
            if not str(merged.get(key) or "").strip() and str(secondary.get(key) or "").strip():
                merged[key] = secondary.get(key)
        for key in ("themes", "symbols", "visual_tags", "objects"):
            primary_value = merged.get(key)
            if not isinstance(primary_value, list) or not primary_value:
                secondary_value = secondary.get(key)
                if isinstance(secondary_value, list):
                    merged[key] = secondary_value
        if float(merged.get("confidence_score") or 0.0) <= 0:
            merged["confidence_score"] = secondary.get("confidence_score") or 0.0
        return merged

    @staticmethod
    def _extract_http_error_message(exc: httpx.HTTPStatusError) -> str:
        response = exc.response
        if response is None:
            return str(exc)
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or "").strip() or response.text.strip() or str(exc)
        return response.text.strip() or str(exc)

    @staticmethod
    def _looks_like_unsupported_image_input(error_message: str) -> bool:
        normalized = str(error_message or "").lower()
        return "image_url" in normalized or "image input" in normalized or "expected `text`" in normalized

    @staticmethod
    def _build_fallback_visual_summary(related_text: str, ocr_text: str, trigger_source: str, error_message: str) -> str:
        if ocr_text:
            return f"已保留关键帧，OCR 提取到画面文本，可结合口播复核。"
        if related_text:
            prefix = "关键帧与口播提示词对齐" if trigger_source == "cue" else "关键帧已保留"
            return f"{prefix}，当前视觉模型未成功解析画面；可结合关联口播复核：{related_text[:120]}"
        if error_message:
            return "关键帧已保留，但当前视觉模型未成功解析画面内容。"
        return "关键帧已保留，待补充视觉识别。"

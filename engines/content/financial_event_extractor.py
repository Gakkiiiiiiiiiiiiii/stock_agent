from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from app.model_providers import AnalysisModelClient
from engines.content.financial_entity_normalizer import FinancialEntityNormalizer


logger = logging.getLogger(__name__)


EVENT_TYPE_KEYWORDS = {
    "PRICE_LEVEL": ("支撑", "压力", "阻力", "跌破", "站稳", "目标位"),
    "TECHNICAL_TREND": ("趋势", "反弹", "回调", "突破", "新高", "新低"),
    "TECHNICAL_INDICATOR": ("MACD", "均线", "成交量", "背离", "缺口"),
    "INDUSTRY_LOGIC": ("产业链", "逻辑", "景气", "渗透率", "供需"),
    "MACRO_INDICATOR": ("通胀", "利率", "降息", "流动性", "PPI", "CPI", "非农"),
    "TRADING_ACTION": ("买", "卖", "减仓", "加仓", "止盈", "止损"),
    "RISK": ("风险", "波动", "补跌", "承压", "不确定", "证伪"),
    "CATALYST": ("催化", "驱动", "受益", "利好", "提振"),
}


class FinancialEventExtractor:
    def __init__(
        self,
        model_client: AnalysisModelClient | None = None,
        entity_normalizer: FinancialEntityNormalizer | None = None,
    ) -> None:
        self.model_client = model_client or AnalysisModelClient()
        self.entity_normalizer = entity_normalizer or FinancialEntityNormalizer()
        self.max_llm_chunks = max(0, int(os.getenv("VIDEO_EVENT_LLM_MAX_CHUNKS", "40")))

    def extract(self, metadata: dict, chunks: list[dict]) -> tuple[str, list[dict]]:
        video_type = self._classify_video_type(metadata=metadata, chunks=chunks)
        llm_available = self.model_client.available()
        if llm_available and self.max_llm_chunks and len(chunks) > self.max_llm_chunks:
            logger.warning(
                "视频分块数 %d 超过 LLM 事件抽取上限 %d，超出部分回退为规则抽取",
                len(chunks),
                self.max_llm_chunks,
            )
        events: list[dict] = []
        for index, chunk in enumerate(chunks):
            use_llm = llm_available and (not self.max_llm_chunks or index < self.max_llm_chunks)
            extracted = self._extract_chunk_events(
                metadata=metadata,
                chunk=chunk,
                video_type=video_type,
                use_llm=use_llm,
            )
            events.extend(extracted)
        if not events:
            events = self._extract_fallback_video_event(metadata=metadata, chunks=chunks, video_type=video_type)
        return video_type, events

    def _extract_chunk_events(self, metadata: dict, chunk: dict, video_type: str, use_llm: bool) -> list[dict]:
        if use_llm and self.model_client.available():
            try:
                payload = self._extract_chunk_events_with_llm(metadata=metadata, chunk=chunk, video_type=video_type)
                if payload:
                    return self._normalize_events(payload, metadata=metadata, chunk=chunk)
            except Exception:
                logger.warning(
                    "LLM 事件抽取失败（chunk_index=%s），回退为规则抽取",
                    chunk.get("chunk_index"),
                    exc_info=True,
                )
        return self._extract_chunk_events_with_rules(metadata=metadata, chunk=chunk, video_type=video_type)

    def _extract_chunk_events_with_llm(self, metadata: dict, chunk: dict, video_type: str) -> list[dict]:
        prompt = (
            "请从下面的金融视频片段中抽取高召回结构化事件，输出 JSON 数组。"
            "每个事件字段必须包含：event_type, claim_type, sentiment, statement, condition_text, invalidation_text, "
            "time_expression, certainty, confidence_score, attributes。"
            "不要把观点改写成事实，不要忽略否定、条件和证伪语句。"
            "如果画面只是证据，不要把图表点位直接当成口播结论。\n"
            f"video_title: {metadata.get('title', '')}\n"
            f"video_type: {video_type}\n"
            f"chunk_topic: {chunk.get('topic', '')}\n"
            f"chunk_timerange_ms: {chunk.get('start_ms')} - {chunk.get('end_ms')}\n"
            f"transcript:\n{chunk.get('transcript_text', '')}\n\n"
            f"ocr_text:\n{chunk.get('ocr_text', '')}\n\n"
            f"visual_focus:\n{chunk.get('visual_focus', '')}\n"
        )
        response = self.model_client.complete(
            prompt=prompt,
            system="你是金融视频事件抽取器，只返回 JSON。",
            temperature=0.1,
        )
        return self._parse_json_array(str(response.get("content") or ""))

    def _extract_chunk_events_with_rules(self, metadata: dict, chunk: dict, video_type: str) -> list[dict]:
        _ = video_type
        transcript_text = str(chunk.get("transcript_text") or "").strip()
        ocr_text = str(chunk.get("ocr_text") or "").strip()
        sentences = [part.strip() for part in re.split(r"[。！？\n]", transcript_text) if part.strip()]
        events: list[dict] = []
        for sentence in sentences:
            if len(sentence) < 4:
                continue
            event_type = self._infer_event_type(sentence=sentence, ocr_text=ocr_text)
            claim_type = self._infer_claim_type(sentence)
            sentiment = self._infer_sentiment(sentence)
            condition_text = self._extract_condition(sentence)
            invalidation_text = self._extract_invalidation(sentence)
            time_payload = self.entity_normalizer.normalize_time_expression(sentence, metadata.get("publish_time"))
            attributes = self._extract_attributes(sentence, ocr_text)
            confidence = self._estimate_event_confidence(sentence=sentence, chunk=chunk, event_type=event_type)
            evidence = self._build_evidence(sentence=sentence, chunk=chunk)
            entities = self.entity_normalizer.extract_entities(sentence, ocr_text, chunk.get("topic") or "")
            if not self._is_eventful(sentence=sentence, event_type=event_type, entities=entities, attributes=attributes):
                continue
            events.append(
                {
                    "chunk_index": chunk.get("chunk_index"),
                    "event_type": event_type,
                    "claim_type": claim_type,
                    "sentiment": sentiment,
                    "subjectivity": "HIGH" if claim_type in {"OPINION", "FORECAST"} else "LOW",
                    "certainty": 0.55 if claim_type == "FORECAST" else 0.72,
                    "confidence_score": confidence,
                    "statement": sentence,
                    "time_expression": time_payload.get("time_expression"),
                    "normalized_time_start": time_payload.get("normalized_time_start"),
                    "normalized_time_end": time_payload.get("normalized_time_end"),
                    "start_ms": chunk.get("start_ms"),
                    "end_ms": chunk.get("end_ms"),
                    "condition_text": condition_text,
                    "invalidation_text": invalidation_text,
                    "entities": entities,
                    "attributes": attributes,
                    "evidence": evidence,
                }
            )
        return events

    def _extract_fallback_video_event(self, metadata: dict, chunks: list[dict], video_type: str) -> list[dict]:
        text = " ".join(str(chunk.get("transcript_text") or "") for chunk in chunks[:2]).strip()
        if not text:
            text = str(metadata.get("title") or "")
        return [
            {
                "chunk_index": chunks[0].get("chunk_index") if chunks else 0,
                "event_type": "OPINION",
                "claim_type": "OPINION",
                "sentiment": self._infer_sentiment(text),
                "subjectivity": "MEDIUM",
                "certainty": 0.5,
                "confidence_score": 0.45,
                "statement": text[:200],
                "time_expression": None,
                "normalized_time_start": None,
                "normalized_time_end": None,
                "start_ms": chunks[0].get("start_ms") if chunks else 0,
                "end_ms": chunks[0].get("end_ms") if chunks else 0,
                "condition_text": self._extract_condition(text),
                "invalidation_text": self._extract_invalidation(text),
                "entities": self.entity_normalizer.extract_entities(text),
                "attributes": {"video_type": video_type},
                "evidence": self._build_evidence(sentence=text[:200], chunk=chunks[0] if chunks else {}),
            }
        ]

    def _normalize_events(self, payload: list[dict], metadata: dict, chunk: dict) -> list[dict]:
        normalized: list[dict] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement") or "").strip()
            if not statement:
                continue
            time_payload = self.entity_normalizer.normalize_time_expression(
                str(item.get("time_expression") or statement),
                metadata.get("publish_time"),
            )
            entities = item.get("entities")
            if not isinstance(entities, list) or not entities:
                entities = self.entity_normalizer.extract_entities(statement, chunk.get("ocr_text") or "", chunk.get("topic") or "")
            normalized.append(
                {
                    "chunk_index": chunk.get("chunk_index"),
                    "event_type": str(item.get("event_type") or self._infer_event_type(statement, chunk.get("ocr_text") or "")),
                    "claim_type": str(item.get("claim_type") or self._infer_claim_type(statement)),
                    "sentiment": str(item.get("sentiment") or self._infer_sentiment(statement)),
                    "subjectivity": str(item.get("subjectivity") or ("HIGH" if "我" in statement else "MEDIUM")),
                    "certainty": self._clamp_score(item.get("certainty"), default=0.7),
                    "confidence_score": self._clamp_score(item.get("confidence_score"), default=0.7),
                    "statement": statement,
                    "time_expression": time_payload.get("time_expression"),
                    "normalized_time_start": time_payload.get("normalized_time_start"),
                    "normalized_time_end": time_payload.get("normalized_time_end"),
                    "start_ms": int(item.get("start_ms") or chunk.get("start_ms") or 0),
                    "end_ms": int(item.get("end_ms") or chunk.get("end_ms") or 0),
                    "condition_text": str(item.get("condition_text") or self._extract_condition(statement) or "").strip() or None,
                    "invalidation_text": str(item.get("invalidation_text") or self._extract_invalidation(statement) or "").strip() or None,
                    "entities": entities,
                    "attributes": item.get("attributes") if isinstance(item.get("attributes"), dict) else self._extract_attributes(statement, chunk.get("ocr_text") or ""),
                    "evidence": self._build_evidence(sentence=statement, chunk=chunk),
                }
            )
        return normalized

    def _build_evidence(self, sentence: str, chunk: dict) -> list[dict]:
        evidence = [
            {
                "source_type": "ASR",
                "source_id": f"chunk_{chunk.get('chunk_index', 0)}",
                "text": sentence,
                "timestamp_ms": chunk.get("start_ms"),
                "confidence_score": chunk.get("confidence_score"),
            }
        ]
        for frame in chunk.get("frame_refs") or []:
            frame_text = str(frame.get("visual_summary") or frame.get("ocr_text") or "").strip()
            if not frame_text:
                continue
            evidence.append(
                {
                    "source_type": "OCR",
                    "source_id": f"frame_{frame.get('frame_index')}",
                    "text": frame_text,
                    "timestamp_ms": frame.get("timestamp_ms"),
                    "image_path": frame.get("image_path"),
                    "confidence_score": chunk.get("confidence_score"),
                }
            )
        return evidence[:4]

    @staticmethod
    def _classify_video_type(metadata: dict, chunks: list[dict]) -> str:
        text = " ".join(
            [
                str(metadata.get("title") or ""),
                str(metadata.get("description") or ""),
                *(str(chunk.get("transcript_text") or "")[:300] for chunk in chunks[:2]),
            ]
        )
        if any(token in text for token in ("支撑", "压力", "K线", "均线", "MACD")):
            return "EQUITY_TECHNICAL_ANALYSIS"
        if any(token in text for token in ("业绩", "估值", "利润", "营收")):
            return "EQUITY_FUNDAMENTAL_ANALYSIS"
        if any(token in text for token in ("CPI", "PPI", "美联储", "利率", "非农")):
            return "MACRO_ANALYSIS"
        if any(token in text for token in ("复盘", "收评", "午评", "盘前")):
            return "MARKET_REVIEW"
        if any(token in text for token in ("电话会", "财报")):
            return "EARNINGS_CALL"
        return "GENERAL_FINANCE"

    @staticmethod
    def _infer_event_type(sentence: str, ocr_text: str) -> str:
        merged = f"{sentence} {ocr_text}"
        for event_type, keywords in EVENT_TYPE_KEYWORDS.items():
            if any(keyword in merged for keyword in keywords):
                return event_type
        return "OPINION"

    @staticmethod
    def _infer_claim_type(sentence: str) -> str:
        if any(token in sentence for token in ("据说", "传闻", "听说")):
            return "RUMOR"
        if any(token in sentence for token in ("预计", "预期", "大概率", "可能", "明天", "下周")):
            return "FORECAST"
        if any(token in sentence for token in ("我认为", "我觉得", "看好", "看空", "建议")):
            return "OPINION"
        if any(token in sentence for token in ("数据显示", "已经", "公布", "同比", "环比")):
            return "FACT"
        return "OPINION"

    @staticmethod
    def _infer_sentiment(sentence: str) -> str:
        if any(token in sentence for token in ("不认为", "看空", "下跌", "承压", "风险", "失效", "跌破", "补跌")):
            return "BEARISH"
        if any(token in sentence for token in ("看多", "反弹", "突破", "新高", "支撑", "利好", "催化")):
            return "BULLISH"
        return "NEUTRAL"

    @staticmethod
    def _extract_condition(sentence: str) -> str | None:
        patterns = [
            r"(只有.+?才.+)",
            r"(如果.+?(?:那么|就).+)",
            r"(前提是.+)",
            r"(条件是.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, sentence)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _extract_invalidation(sentence: str) -> str | None:
        patterns = [
            r"(跌破.+?(?:失效|就坏了|不成立))",
            r"(如果.+?失效.+)",
            r"(证伪.+)",
            r"(无效.+)",
            r"(不能破.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, sentence)
            if match:
                return match.group(1).strip()
        return None

    def _extract_attributes(self, sentence: str, ocr_text: str) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        number_match = re.findall(r"\d+(?:\.\d+)?", sentence)
        if number_match:
            attributes["numbers"] = [float(item) if "." in item else int(item) for item in number_match[:6]]
        if any(token in sentence for token in ("支撑", "压力", "阻力")):
            attributes["level_type"] = "SUPPORT" if "支撑" in sentence else "RESISTANCE"
        if any(token in f"{sentence} {ocr_text}" for token in ("日线", "周线", "月线", "分时")):
            cycle = next(token for token in ("日线", "周线", "月线", "分时") if token in f"{sentence} {ocr_text}")
            attributes["cycle"] = cycle
        return attributes

    def _estimate_event_confidence(self, sentence: str, chunk: dict, event_type: str) -> float:
        score = 0.55
        if chunk.get("ocr_text"):
            score += 0.08
        if chunk.get("visual_focus"):
            score += 0.06
        if any(token in sentence for token in ("支撑", "压力", "风险", "催化", "突破")):
            score += 0.08
        if event_type != "OPINION":
            score += 0.05
        return round(min(score, 0.92), 4)

    @staticmethod
    def _is_eventful(sentence: str, event_type: str, entities: list[dict], attributes: dict[str, Any]) -> bool:
        if event_type != "OPINION":
            return True
        if entities:
            return True
        if attributes.get("numbers"):
            return True
        return any(token in sentence for token in ("看多", "看空", "建议", "风险", "催化"))

    @staticmethod
    def _clamp_score(value: Any, default: float) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(score, 1.0))

    @staticmethod
    def _parse_json_array(content: str) -> list[dict]:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        payload = json.loads(text[start : end + 1])
        return payload if isinstance(payload, list) else []

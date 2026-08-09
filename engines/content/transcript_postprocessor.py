from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from financial_agent.utils import project_root


logger = logging.getLogger(__name__)

ASR_TERM_CORRECTIONS_PATH = Path("config") / "asr_term_corrections.yaml"

# correction_trace 类型（设计文档 P1-3 / §53）：所有文本变换必须可审计。
TRACE_TYPE_FORMAT_NORMALIZATION = "FORMAT_NORMALIZATION"
TRACE_TYPE_SCRIPT_CONVERSION = "SCRIPT_CONVERSION"
TRACE_TYPE_DICTIONARY_CORRECTION = "DICTIONARY_CORRECTION"

# 内置最小纠错表：config/asr_term_corrections.yaml 缺失或解析失败时使用。
DEFAULT_TERM_CORRECTIONS = {
    "K 线": "K线",
    "M A C D": "MACD",
    "市盈 率": "市盈率",
    "成交 量": "成交量",
    "支 撑": "支撑",
    "压 力": "压力",
}


class TranscriptPostprocessor:
    def __init__(self, corrections_path: Path | None = None) -> None:
        self.term_corrections = self._load_term_corrections(corrections_path)
        self._opencc_converter = None
        self._opencc_unavailable = False

    def normalize(self, transcript: dict, metadata: dict | None = None) -> dict:
        normalized_segments = []
        text_parts: list[str] = []
        for segment in transcript.get("segments", []):
            raw_text = str(segment.get("text", ""))
            text, corrections = self._normalize_text_with_trace(raw_text)
            entities = self._extract_entity_hints(text)
            time_expressions = self._extract_time_hints(text)
            numeric_hints = self._extract_numeric_hints(text)
            rhetoric = self._extract_rhetoric_flags(text)
            text_parts.append(text)
            normalized_segments.append(
                dict(segment)
                | {
                    "text": text,
                    "raw_text": segment.get("raw_text", raw_text),
                    "normalized_text": text,
                    "correction_trace": corrections,
                    "entity_hints": entities,
                    "time_hints": time_expressions,
                    "numeric_hints": numeric_hints,
                    "rhetoric_flags": rhetoric,
                }
            )
        return transcript | {
            "text": "\n".join(part for part in text_parts if part).strip(),
            "segments": normalized_segments,
            "metadata_hints": {
                "title": (metadata or {}).get("title"),
                "publish_time": (metadata or {}).get("publish_time"),
            },
        }

    def _normalize_text(self, value: str) -> str:
        return self._normalize_text_with_trace(value)[0]

    def _normalize_text_with_trace(self, value: str) -> tuple[str, list[dict]]:
        corrections: list[dict] = []
        original = value or ""
        compact = self._convert_traditional_to_simplified(original)
        if compact != original:
            corrections.append(
                {
                    "type": TRACE_TYPE_SCRIPT_CONVERSION,
                    "from": original,
                    "to": compact,
                    "method": "opencc_t2s",
                    "confidence": 1.0,
                }
            )
        compact = self._apply_regex(compact, r"\s+", " ", corrections, "whitespace_collapse")
        stripped = compact.strip()
        if stripped != compact:
            corrections.append(
                {
                    "type": TRACE_TYPE_FORMAT_NORMALIZATION,
                    "from": compact,
                    "to": stripped,
                    "method": "whitespace_strip",
                    "confidence": 1.0,
                }
            )
        compact = stripped
        for filler in (" 呃 ", " 啊 "):
            compact = self._apply_replace(compact, filler, " ", corrections, "filler_word_removal")
        for wrong, correct in self.term_corrections.items():
            if wrong in compact:
                compact = compact.replace(wrong, correct)
                corrections.append(
                    {
                        "type": TRACE_TYPE_DICTIONARY_CORRECTION,
                        "from": wrong,
                        "to": correct,
                        "method": "term_dictionary",
                        "confidence": 1.0,
                    }
                )
        compact = self._apply_regex(compact, r"(\d)\s+(\d)", r"\1\2", corrections, "numeric_space_merge")
        compact = self._apply_regex(compact, r"百分之\s*(\d+(?:\.\d+)?)", r"\1%", corrections, "percent_wording")
        compact = self._apply_regex(compact, r"(港|美|人) 元", r"\1元", corrections, "currency_wording")
        compact = self._apply_regex(compact, r"([上下中]) 证", r"\1证", corrections, "index_wording")
        return compact, corrections

    @staticmethod
    def _apply_regex(value: str, pattern: str, repl: str, corrections: list[dict], method: str) -> str:
        def _sub(match: re.Match) -> str:
            original = match.group(0)
            replaced = match.expand(repl)
            if replaced != original:
                corrections.append(
                    {
                        "type": TRACE_TYPE_FORMAT_NORMALIZATION,
                        "from": original,
                        "to": replaced,
                        "method": method,
                        "confidence": 1.0,
                    }
                )
            return replaced

        return re.sub(pattern, _sub, value)

    @staticmethod
    def _apply_replace(value: str, old: str, new: str, corrections: list[dict], method: str) -> str:
        if old not in value:
            return value
        corrections.append(
            {
                "type": TRACE_TYPE_FORMAT_NORMALIZATION,
                "from": old,
                "to": new,
                "method": method,
                "confidence": 1.0,
            }
        )
        return value.replace(old, new)

    def _convert_traditional_to_simplified(self, value: str) -> str:
        if not value:
            return value
        converter = self._get_opencc_converter()
        if converter is None:
            return value
        try:
            return converter.convert(value)
        except Exception:
            logger.warning("opencc 繁转简失败，保留原文", exc_info=True)
            return value

    def _get_opencc_converter(self):
        if self._opencc_converter is not None or self._opencc_unavailable:
            return self._opencc_converter
        try:
            from opencc import OpenCC
        except ImportError:
            logger.warning("opencc-python-reimplemented 未安装，ASR 转写跳过繁转简处理")
            self._opencc_unavailable = True
            return None
        self._opencc_converter = OpenCC("t2s")
        return self._opencc_converter

    @staticmethod
    def _load_term_corrections(corrections_path: Path | None = None) -> dict[str, str]:
        path = corrections_path or (project_root() / ASR_TERM_CORRECTIONS_PATH)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.warning("ASR 术语纠错表加载失败（%s），回退内置最小表", path, exc_info=True)
            return dict(DEFAULT_TERM_CORRECTIONS)
        corrections = data.get("corrections")
        if not isinstance(corrections, dict) or not corrections:
            logger.warning("ASR 术语纠错表为空或格式不正确（%s），回退内置最小表", path)
            return dict(DEFAULT_TERM_CORRECTIONS)
        merged = dict(DEFAULT_TERM_CORRECTIONS)
        merged.update({str(wrong): str(correct) for wrong, correct in corrections.items()})
        return merged

    @staticmethod
    def _extract_entity_hints(text: str) -> list[str]:
        hints = re.findall(r"\b\d{6}\b|\b\d{4}\.HK\b|上证指数|深证成指|创业板|恒生科技|黄金|原油|美联储|半导体|AI", text, flags=re.IGNORECASE)
        return [str(item).strip() for item in hints if str(item).strip()]

    @staticmethod
    def _extract_time_hints(text: str) -> list[str]:
        hints = []
        for token in ("今天", "明天", "下周", "本周", "近期", "短期", "中期", "长期"):
            if token in text:
                hints.append(token)
        return hints

    @staticmethod
    def _extract_numeric_hints(text: str) -> list[str]:
        return re.findall(r"\d+(?:\.\d+)?%?|\d+月\d+日", text)

    @staticmethod
    def _extract_rhetoric_flags(text: str) -> list[str]:
        flags = []
        if any(token in text for token in ("我认为", "我觉得", "看好", "看空", "建议")):
            flags.append("opinion")
        if any(token in text for token in ("预计", "预期", "大概率", "可能")):
            flags.append("forecast")
        if any(token in text for token in ("不", "并不", "尚未", "没有")):
            flags.append("negation")
        if any(token in text for token in ("如果", "只有", "前提", "条件")):
            flags.append("conditional")
        return flags

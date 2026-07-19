from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from financial_agent.utils import project_root


logger = logging.getLogger(__name__)

ASR_TERM_CORRECTIONS_PATH = Path("config") / "asr_term_corrections.yaml"

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
            text = self._normalize_text(segment.get("text", ""))
            entities = self._extract_entity_hints(text)
            time_expressions = self._extract_time_hints(text)
            numeric_hints = self._extract_numeric_hints(text)
            rhetoric = self._extract_rhetoric_flags(text)
            text_parts.append(text)
            normalized_segments.append(
                dict(segment)
                | {
                    "text": text,
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
        compact = self._convert_traditional_to_simplified(value or "")
        compact = re.sub(r"\s+", " ", compact).strip()
        compact = compact.replace(" 呃 ", " ").replace(" 啊 ", " ")
        for wrong, correct in self.term_corrections.items():
            compact = compact.replace(wrong, correct)
        compact = re.sub(r"(\d)\s+(\d)", r"\1\2", compact)
        compact = re.sub(r"百分之\s*(\d+(?:\.\d+)?)", r"\1%", compact)
        compact = re.sub(r"(港|美|人) 元", r"\1元", compact)
        compact = re.sub(r"([上下中]) 证", r"\1证", compact)
        return compact

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

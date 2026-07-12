from __future__ import annotations

import re


class TranscriptPostprocessor:
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

    @staticmethod
    def _normalize_text(value: str) -> str:
        compact = re.sub(r"\s+", " ", value or "").strip()
        compact = compact.replace(" 呃 ", " ").replace(" 啊 ", " ")
        compact = compact.replace("K 线", "K线").replace("M A C D", "MACD")
        compact = compact.replace("市盈 率", "市盈率").replace("成交 量", "成交量")
        compact = compact.replace("支 撑", "支撑").replace("压 力", "压力")
        compact = re.sub(r"(\d)\s+(\d)", r"\1\2", compact)
        compact = re.sub(r"百分之\s*(\d+(?:\.\d+)?)", r"\1%", compact)
        compact = re.sub(r"(港|美|人) 元", r"\1元", compact)
        compact = re.sub(r"([上下中]) 证", r"\1证", compact)
        return compact

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

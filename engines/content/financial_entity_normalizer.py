from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta


COMMON_ENTITY_ALIASES = {
    "上证指数": {"entity_type": "INDEX", "ticker": "000001.SH"},
    "沪指": {"entity_type": "INDEX", "ticker": "000001.SH"},
    "深证成指": {"entity_type": "INDEX", "ticker": "399001.SZ"},
    "创业板": {"entity_type": "INDEX", "ticker": "399006.SZ"},
    "恒生科技": {"entity_type": "INDEX", "ticker": "HSTECH"},
    "黄金": {"entity_type": "COMMODITY", "ticker": "XAUUSD"},
    "原油": {"entity_type": "COMMODITY", "ticker": "CL"},
    "美联储": {"entity_type": "MACRO", "ticker": "FED"},
    "半导体": {"entity_type": "INDUSTRY", "ticker": "SEMI"},
    "AI": {"entity_type": "THEME", "ticker": "AI"},
}


class FinancialEntityNormalizer:
    def extract_entities(self, *texts: str) -> list[dict]:
        joined = " ".join(str(text or "") for text in texts if str(text or "").strip())
        results: list[dict] = []
        seen: set[str] = set()
        for alias, payload in COMMON_ENTITY_ALIASES.items():
            if alias not in joined:
                continue
            entity_id = payload["ticker"]
            if entity_id in seen:
                continue
            results.append(
                {
                    "name": alias,
                    "ticker": payload["ticker"],
                    "entity_type": payload["entity_type"],
                }
            )
            seen.add(entity_id)
        for code in re.findall(r"\b\d{6}\b|\b\d{4}\.HK\b|\b[A-Z]{1,5}\b", joined, flags=re.IGNORECASE):
            normalized = code.upper()
            if normalized in seen:
                continue
            if normalized.isalpha() and len(normalized) <= 2:
                continue
            results.append(
                {
                    "name": normalized,
                    "ticker": normalized,
                    "entity_type": self._infer_entity_type(normalized),
                }
            )
            seen.add(normalized)
        return results

    def normalize_time_expression(self, text: str, publish_time: str | None) -> dict[str, str | None]:
        normalized = str(text or "").strip()
        if not normalized:
            return {"time_expression": None, "normalized_time_start": None, "normalized_time_end": None}
        anchor = self._parse_publish_date(publish_time)
        if anchor is None:
            return {"time_expression": normalized, "normalized_time_start": None, "normalized_time_end": None}
        if "下周" in normalized:
            start = anchor + timedelta(days=(7 - anchor.weekday()))
            end = start + timedelta(days=6)
            return self._build_time_payload(normalized, start, end)
        if "本周" in normalized or "这周" in normalized:
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6)
            return self._build_time_payload(normalized, start, end)
        if "明天" in normalized:
            target = anchor + timedelta(days=1)
            return self._build_time_payload(normalized, target, target)
        if "今天" in normalized:
            return self._build_time_payload(normalized, anchor, anchor)
        if "短期" in normalized or "近期" in normalized:
            return {"time_expression": normalized, "normalized_time_start": None, "normalized_time_end": None}
        date_match = re.search(r"(20\d{2})[年/-]?(0?\d{1,2})[月/-]?(0?\d{1,2})日?", normalized)
        if date_match:
            try:
                target = datetime(
                    int(date_match.group(1)),
                    int(date_match.group(2)),
                    int(date_match.group(3)),
                    tzinfo=UTC,
                )
            except ValueError:
                return {"time_expression": normalized, "normalized_time_start": None, "normalized_time_end": None}
            return self._build_time_payload(normalized, target, target)
        return {"time_expression": normalized, "normalized_time_start": None, "normalized_time_end": None}

    @staticmethod
    def normalize_numeric_text(text: str) -> str:
        normalized = str(text or "")
        replacements = {
            "一": "1",
            "二": "2",
            "两": "2",
            "三": "3",
            "四": "4",
            "五": "5",
            "六": "6",
            "七": "7",
            "八": "8",
            "九": "9",
            "零": "0",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        normalized = normalized.replace("百分之", "")
        return normalized

    @staticmethod
    def infer_themes(text: str, known_themes: list[str] | None = None) -> list[str]:
        candidates = []
        merged = str(text or "")
        for theme in known_themes or []:
            if theme and theme in merged:
                candidates.append(theme)
        for fallback in ("黄金", "半导体", "AI", "消费", "券商", "银行", "新能源", "医药"):
            if fallback in merged and fallback not in candidates:
                candidates.append(fallback)
        return candidates

    @staticmethod
    def _infer_entity_type(value: str) -> str:
        if re.fullmatch(r"\d{4}\.HK", value):
            return "EQUITY"
        if re.fullmatch(r"\d{6}", value):
            return "EQUITY"
        if value in {"FED", "SEMI", "AI"}:
            return "THEME"
        return "UNKNOWN"

    @staticmethod
    def _parse_publish_date(raw_value: str | None) -> datetime | None:
        text = str(raw_value or "").strip()
        if len(text) == 8 and text.isdigit():
            try:
                return datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC)
            except ValueError:
                return None
        return None

    @staticmethod
    def _build_time_payload(raw_expression: str, start: datetime, end: datetime) -> dict[str, str]:
        return {
            "time_expression": raw_expression,
            "normalized_time_start": start.date().isoformat(),
            "normalized_time_end": end.date().isoformat(),
        }

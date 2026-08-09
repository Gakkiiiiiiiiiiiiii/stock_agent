from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol

# ExternalVerificationResult dict 约定（§25）：
# {
#   "status": "MATCH" | "CONFLICT" | "NOT_FOUND" | "ERROR",
#   "source_type": "MARKET_DATA" | "OFFICIAL_FILING" | "FUNDAMENTAL" | "POLICY_SOURCE",
#   "provider": "qmt" | "ifind" | ...,
#   "source_id": "600030.SH" / 公告编号 / 政策文号,
#   "as_of": "2026-08-01",
#   "observed_value": 120.3,
#   "unit": "CNY" | "CNY_100M" | "PERCENT" | None,
#   "provenance": {...},
# }
RESULT_STATUSES = {"MATCH", "CONFLICT", "NOT_FOUND", "ERROR"}


class AuthoritativeVerificationProvider(Protocol):
    """权威数据源验证 Provider 协议（P0-7 / §25）。

    supports() 决定路由；verify() 只回答「客观上是否为真」，
    不得承诺任何关于视频证据支持的结论。
    """

    def supports(self, unit: dict[str, Any]) -> bool:
        ...

    def verify(self, unit: dict[str, Any]) -> dict[str, Any]:
        ...


def make_result(
    status: str,
    *,
    source_type: str | None = None,
    provider: str | None = None,
    source_id: str | None = None,
    as_of: str | None = None,
    observed_value: Any = None,
    unit: str | None = None,
    provenance: dict | None = None,
    **extra: Any,
) -> dict[str, Any]:
    status = str(status or "NOT_FOUND").upper()
    if status not in RESULT_STATUSES:
        status = "ERROR"
    return {
        "status": status,
        "source_type": source_type,
        "provider": provider,
        "source_id": source_id,
        "as_of": as_of,
        "observed_value": observed_value,
        "unit": unit,
        "provenance": provenance or {},
        **extra,
    }


def extract_ticker(unit: dict[str, Any]) -> str | None:
    """从 unit 提取 6 位 A 股代码（entities.ticker > subject_key > statement）。"""
    import re

    for entity in unit.get("entities") or []:
        ticker = str(entity.get("ticker") or "").strip()
        if re.fullmatch(r"\d{6}", ticker):
            return ticker
    for candidate in (unit.get("subject_key"), unit.get("subject_name")):
        text = str(candidate or "").strip()
        if re.fullmatch(r"\d{6}", text):
            return text
    match = re.search(r"\b\d{6}\b", str(unit.get("statement") or ""))
    return match.group(0) if match else None


def claim_as_of(unit: dict[str, Any]) -> date | None:
    """解析 unit.as_of_time 为 date；无法解析返回 None（§6.1）。

    支持 datetime / date / "%Y%m%d" / ISO 字符串（含 "Z" 后缀）。
    """
    value = unit.get("as_of_time")

    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()

    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()

    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")
        ).date()
    except ValueError:
        return None


def claim_numbers(text: str) -> list[float]:
    """解析 claim 中的数值；优先使用 financial_numeric（P1-1），缺失时退回 regex。"""
    try:
        from engines.content.financial_numeric import parse_financial_numerics

        values: list[float] = []
        for item in parse_financial_numerics(text):
            # §9：FinancialNumericValue dataclass 必须通过 .value 读取，
            # 否则结构化结果被忽略、落 regex fallback（中文数字丢失、可能抓到 6 位代码）。
            value = getattr(item, "value", None)
            if value is None and isinstance(item, dict):
                value = item.get("value")
            elif value is None and isinstance(item, (int, float)):
                value = item
            if value is not None:
                values.append(float(value))
        if values:
            return values
    except ImportError:
        pass
    except Exception:
        pass
    import re

    return [float(token) for token in re.findall(r"\d+(?:\.\d+)?", text or "")]

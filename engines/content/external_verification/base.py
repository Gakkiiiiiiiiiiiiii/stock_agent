from __future__ import annotations

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


def claim_numbers(text: str) -> list[float]:
    """解析 claim 中的数值；优先使用 financial_numeric（P1-1），缺失时退回 regex。"""
    try:
        from engines.content.financial_numeric import parse_financial_numerics

        values: list[float] = []
        for item in parse_financial_numerics(text):
            if isinstance(item, dict) and item.get("value") is not None:
                values.append(float(item["value"]))
            elif isinstance(item, (int, float)):
                values.append(float(item))
        if values:
            return values
    except ImportError:
        pass
    except Exception:
        pass
    import re

    return [float(token) for token in re.findall(r"\d+(?:\.\d+)?", text or "")]

from __future__ import annotations


def rank_sector_strength(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: item.get("strength_score", 0), reverse=True)


def sample_sector_strength() -> list[dict]:
    return [
        {"sector": "AI机房液冷", "strength_score": 82, "reason": "成交额放大且核心标的走强"},
        {"sector": "黄金", "strength_score": 76, "reason": "实际利率下行预期与避险需求共振"},
        {"sector": "钠离子电池", "strength_score": 68, "reason": "产业逻辑明确，价格强度中等"},
    ]


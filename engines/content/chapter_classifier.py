from __future__ import annotations

import re


DOMAIN_RULES = [
    ("MACRO", "MACRO_ANALYSIS", ("CPI", "PPI", "PMI", "美联储", "降息", "加息", "利率", "通胀", "汇率", "社融", "M1", "M2")),
    ("CAPITAL_FLOW", "CAPITAL_FLOW", ("资金", "成交额", "北向", "南向", "融资", "两融", "流入", "流出", "量化")),
    ("INDUSTRY", "INDUSTRY_ANALYSIS", ("产业链", "供需", "库存", "渗透率", "景气", "产能", "国产替代", "技术路线")),
    ("COMPANY", "COMPANY_ANALYSIS", ("营收", "利润", "业绩", "估值", "ROE", "财报", "订单", "回购")),
    ("TECHNICAL", "TECHNICAL_ANALYSIS", ("K线", "均线", "MACD", "支撑", "压力", "突破", "跌破", "日线", "周线")),
    ("TRADING", "TRADING_PLAN", ("仓位", "买点", "卖点", "加仓", "减仓", "止盈", "止损", "低吸", "观察")),
    ("RISK", "RISK_WARNING", ("风险", "证伪", "失效", "不确定", "补跌", "平仓", "黑天鹅")),
    ("POLITICS_EVENT", "POLITICS_EVENT", ("政策", "监管", "出口管制", "制裁", "关税", "选举", "战争")),
    ("MARKET", "MARKET_REVIEW", ("上证", "创业板", "指数", "市场", "盘面", "涨跌家数", "风格", "复盘")),
]


class ChapterClassifier:
    def classify(self, text: str, ocr_text: str = "", visual_summary: str = "") -> dict:
        merged = f"{text} {ocr_text} {visual_summary}"
        scores: dict[tuple[str, str], int] = {}
        for domain, chapter_type, keywords in DOMAIN_RULES:
            score = sum(merged.count(keyword) for keyword in keywords)
            if score:
                scores[(domain, chapter_type)] = score
        if not scores:
            return {
                "primary_domain": "GENERAL",
                "secondary_domains": [],
                "chapter_type": "OTHER",
                "confidence_score": 0.45,
            }
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        primary_domain, chapter_type = ranked[0][0]
        secondary = [domain for (domain, _), score in ranked[1:4] if score > 0 and domain != primary_domain]
        if self._looks_like_ad(merged):
            return {
                "primary_domain": "GENERAL",
                "secondary_domains": [],
                "chapter_type": "ADVERTISEMENT",
                "confidence_score": 0.72,
            }
        return {
            "primary_domain": primary_domain,
            "secondary_domains": secondary,
            "chapter_type": chapter_type,
            "confidence_score": min(0.95, 0.58 + ranked[0][1] * 0.06),
        }

    @staticmethod
    def infer_title(text: str, domain: str, entities: list[str]) -> str:
        if entities:
            return f"{entities[0]}相关分析"
        for marker in ("上证指数", "创业板", "半导体", "医药", "黄金", "原油", "液冷", "微盘股"):
            if marker in text:
                return f"{marker}分析"
        labels = {
            "MACRO": "宏观分析",
            "MARKET": "市场复盘",
            "CAPITAL_FLOW": "资金流向",
            "INDUSTRY": "行业产业分析",
            "COMPANY": "公司分析",
            "TECHNICAL": "技术分析",
            "TRADING": "交易计划",
            "RISK": "风险提示",
        }
        return labels.get(domain, "综合分析")

    @staticmethod
    def extract_entities(text: str) -> list[str]:
        entities = []
        entities.extend(re.findall(r"\b\d{6}\b|\b\d{4}\.HK\b", text, flags=re.IGNORECASE))
        for marker in ("上证指数", "创业板", "半导体", "医药", "黄金", "原油", "液冷", "微盘股", "美联储"):
            if marker in text and marker not in entities:
                entities.append(marker)
        return entities[:8]

    @staticmethod
    def _looks_like_ad(text: str) -> bool:
        if any(token in text for token in ("点赞", "投币", "一键三连", "扫码", "购买链接", "优惠券")):
            return True
        return "关注" in text and any(token in text for token in ("账号", "频道", "主播", "公众号"))

from __future__ import annotations

from collections import Counter


class VideoAnalysisDocumentGenerator:
    def generate(self, metadata: dict, chapters: list[dict], units: list[dict]) -> dict:
        kind_counts = Counter(str(unit.get("knowledge_kind") or "STATE") for unit in units)
        domains = sorted({str(chapter.get("primary_domain") or "GENERAL") for chapter in chapters})
        core_summary = self._core_summary(metadata, chapters, units)
        markdown = self._markdown(metadata, chapters, units, core_summary)
        confidence_values = [float(unit.get("extraction_confidence") or 0.5) for unit in units] or [0.5]
        return {
            "document_markdown": markdown,
            "core_summary": core_summary,
            "video_type": self._video_type(domains, units),
            "primary_domains": domains,
            "chapter_count": len(chapters),
            "knowledge_unit_count": len(units),
            "method_count": kind_counts["METHOD"] + kind_counts["CONCEPT"],
            "fact_count": kind_counts["FACT"],
            "state_count": kind_counts["STATE"] + kind_counts["TECHNICAL_SIGNAL"],
            "thesis_count": kind_counts["CAUSAL_THESIS"],
            "forecast_count": kind_counts["FORECAST"],
            "action_count": kind_counts["ACTION"],
            "risk_count": kind_counts["RISK_CONDITION"],
            "confidence_score": round(sum(confidence_values) / len(confidence_values), 4),
            "generator_provider": "rule",
            "generator_model": "video-analysis-document-generator",
            "generator_version": "v3.0-rule",
            "schema_version": "v1",
        }

    @staticmethod
    def _core_summary(metadata: dict, chapters: list[dict], units: list[dict]) -> str:
        title = metadata.get("title") or "视频"
        domain_text = "、".join(sorted({chapter.get("primary_domain") for chapter in chapters if chapter.get("primary_domain")}))
        action_count = sum(1 for unit in units if unit.get("knowledge_kind") == "ACTION")
        risk_count = sum(1 for unit in units if unit.get("knowledge_kind") == "RISK_CONDITION")
        return f"{title} 已解析为 {len(chapters)} 个章节、{len(units)} 条原子知识，覆盖 {domain_text or 'GENERAL'}；包含 {action_count} 条操作类知识和 {risk_count} 条风险/证伪知识。"

    @staticmethod
    def _markdown(metadata: dict, chapters: list[dict], units: list[dict], core_summary: str) -> str:
        lines = [
            f"# {metadata.get('title') or '视频分析文档'}",
            "",
            "## 核心摘要",
            core_summary,
            "",
            "## 章节",
        ]
        units_by_chapter: dict[int, list[dict]] = {}
        for unit in units:
            units_by_chapter.setdefault(int(unit.get("chapter_index") or 0), []).append(unit)
        for chapter in chapters:
            index = int(chapter.get("chapter_index") or 0)
            lines.extend(
                [
                    "",
                    f"### {index + 1}. {chapter.get('title')}",
                    f"- 时间：{chapter.get('start_ms')} - {chapter.get('end_ms')} ms",
                    f"- 领域：{chapter.get('primary_domain')} / {chapter.get('chapter_type')}",
                    f"- 摘要：{chapter.get('summary') or ''}",
                    "",
                    "知识单元：",
                ]
            )
            for unit in units_by_chapter.get(index, [])[:20]:
                lines.append(f"- [{unit.get('knowledge_kind')}/{unit.get('lifecycle_status')}] {unit.get('statement')}")
        return "\n".join(lines).strip()

    @staticmethod
    def _video_type(domains: list[str], units: list[dict]) -> str:
        if "TECHNICAL" in domains:
            return "EQUITY_TECHNICAL_ANALYSIS"
        if "MACRO" in domains:
            return "MACRO_ANALYSIS"
        if "INDUSTRY" in domains:
            return "INDUSTRY_RESEARCH"
        if any(unit.get("knowledge_kind") == "ACTION" for unit in units):
            return "MARKET_REVIEW"
        return "GENERAL_FINANCE"

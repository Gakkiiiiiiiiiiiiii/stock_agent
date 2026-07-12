from __future__ import annotations

import re
from pathlib import Path

from financial_agent.utils import project_root


class VideoSummaryMarkdownExporter:
    def __init__(self, export_root: Path | None = None) -> None:
        self.export_root = (export_root or project_root() / "knowledge_base" / "video_summaries").resolve()

    def build_export_path(self, metadata: dict) -> Path:
        publish_time = str(metadata.get("publish_time") or "unknown")
        bvid = str(metadata.get("bvid") or metadata.get("platform_video_id") or "video")
        title_slug = self._slugify(str(metadata.get("title") or "summary"))
        filename = f"{publish_time}_{bvid}_{title_slug}.md"
        return (self.export_root / filename).resolve()

    def export(self, metadata: dict, summary: dict) -> Path:
        output_path = self.build_export_path(metadata)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self._render_markdown(metadata=metadata, summary=summary), encoding="utf-8")
        return output_path

    def resolve_existing_path(self, metadata: dict) -> Path | None:
        path = self.build_export_path(metadata)
        return path if path.exists() else None

    @staticmethod
    def _slugify(value: str) -> str:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value.strip(), flags=re.UNICODE)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned[:80] or "summary"

    @staticmethod
    def _render_markdown(metadata: dict, summary: dict) -> str:
        title = str(metadata.get("title") or "视频总结")
        author = str(metadata.get("author_name") or "")
        publish_time = str(metadata.get("publish_time") or "")
        source_url = str(metadata.get("url") or "")
        llm_model = str(summary.get("llm_model") or "")
        llm_provider = str(summary.get("llm_provider") or "")
        confidence = summary.get("confidence_score")
        evidence = summary.get("evidence_segments") or []

        def render_list(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items if str(item).strip()) or "- 无"

        lines = [
            f"# {title}",
            "",
            "## 元信息",
            f"- 作者：{author or '未知'}",
            f"- 发布时间：{publish_time or '未知'}",
            f"- 来源：{source_url or '未知'}",
            f"- 总结模型：{llm_provider or 'unknown'} / {llm_model or 'unknown'}",
            f"- 视频类型：{summary.get('video_type') or 'GENERAL_FINANCE'}",
            f"- 置信度：{confidence if confidence is not None else '未知'}",
            "",
            "## 核心摘要",
            str(summary.get("core_summary") or "").strip() or "无",
            "",
            "## 看多观点",
            render_list(summary.get("bull_points") or []),
            "",
            "## 看空观点",
            render_list(summary.get("bear_points") or []),
            "",
            "## 主题",
            render_list(summary.get("themes") or []),
            "",
            "## 标的",
            render_list(summary.get("symbols") or []),
            "",
            "## 催化",
            render_list(summary.get("catalysts") or []),
            "",
            "## 风险",
            render_list(summary.get("risks") or []),
            "",
            "## 事实提要",
            render_list(summary.get("fact_points") or []),
            "",
            "## 预测提要",
            render_list(summary.get("forecast_points") or []),
            "",
            "## 证伪条件",
            render_list(summary.get("invalidation_conditions") or []),
            "",
            "## 操作观点",
            str(summary.get("actionable_view") or "").strip() or "无",
            "",
            "## 时效说明",
            "- 该结论属于时间敏感知识。",
            "- 若与旧视频或旧知识冲突，默认优先采信发布时间更近的总结。",
            "",
            "## 章节摘要",
        ]
        chapter_summaries = summary.get("chapter_summaries") or []
        if chapter_summaries:
            lines.extend(str(item).strip() for item in chapter_summaries if str(item).strip())
        else:
            lines.append("- 无")
        lines.extend(
            [
                "",
            "## 证据片段",
            ]
        )
        if evidence:
            for segment in evidence:
                if not isinstance(segment, dict):
                    lines.append(f"- {segment}")
                    continue
                if segment.get("type") == "visual":
                    lines.append(
                        f"- [视觉] {segment.get('start_ms', 0)} ms：{segment.get('visual_summary') or segment.get('text', '')}"
                    )
                    if segment.get("ocr_text"):
                        lines.append(f"  OCR：{segment.get('ocr_text')}")
                    if segment.get("image_path"):
                        lines.append(f"  图像：{Path(str(segment.get('image_path'))).name}")
                    continue
                lines.append(f"- {segment.get('start_ms', 0)} - {segment.get('end_ms', 0)} ms：{segment.get('text', '')}")
        else:
            lines.append("- 无")
        return "\n".join(lines).strip() + "\n"

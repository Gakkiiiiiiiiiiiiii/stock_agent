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
        ctx = VideoSummaryMarkdownExporter._build_template_context(metadata=metadata, summary=summary)
        lines: list[str] = [f"# {ctx['title']}", ""]

        VideoSummaryMarkdownExporter._append_section(lines, "## 1. 视频元信息")
        VideoSummaryMarkdownExporter._append_bullets(
            lines,
            [
                ("视频ID", ctx["video_id"]),
                ("作者/主讲人", ctx["author"]),
                ("其他发言人", VideoSummaryMarkdownExporter._render_inline_list(ctx["speakers"])),
                ("发布时间", ctx["publish_time"]),
                ("录制时间", ctx["recording_time"]),
                ("数据截止时间", ctx["data_cutoff_time"]),
                ("视频时长", ctx["duration"]),
                ("来源", ctx["source_url"]),
                ("视频类型", ctx["video_type"]),
                ("涉及市场", VideoSummaryMarkdownExporter._render_inline_list(ctx["markets"])),
                ("涉及行业", VideoSummaryMarkdownExporter._render_inline_list(ctx["sectors"])),
                ("涉及主题", VideoSummaryMarkdownExporter._render_inline_list(ctx["themes"])),
                ("涉及标的", VideoSummaryMarkdownExporter._render_inline_list(ctx["symbols"])),
                ("总结模型", f"{ctx['llm_provider']} / {ctx['llm_model']}"),
                ("综合置信度", ctx["confidence_score"]),
                ("时效性等级", ctx["timeliness_level"]),
                ("预计有效期", ctx["valid_until"]),
            ],
        )
        lines.extend(
            [
                "",
                "### 信息标识",
                "",
                "* 【明确表述】：作者直接表达的内容",
                "* 【上下文归纳】：根据作者前后文归纳",
                "* 【模型推断】：总结模型基于视频内容推导",
                "* 【待核验】：尚未通过外部数据验证",
                "* 【识别存疑】：语音、字幕、图表或画面存在歧义",
                "* 【已失效】：相关条件已变化或观点超过有效期",
                "",
                "---",
                "",
                "## 2. 核心摘要",
                "",
                "### 一句话结论",
                "",
                ctx["one_sentence_summary"],
                "",
                "### 核心摘要",
                "",
                ctx["core_summary"],
                "",
                "### 最重要的结论",
                "",
            ]
        )
        for index, item in enumerate(ctx["key_conclusions"][:3], start=1):
            lines.append(f"{index}. {item}")

        lines.extend(
            [
                "",
                "### 视频整体倾向",
                "",
            ]
        )
        VideoSummaryMarkdownExporter._append_bullets(
            lines,
            [
                ("市场方向", ctx["market_bias"]),
                ("核心时间周期", ctx["primary_time_horizon"]),
                ("风险偏好", ctx["risk_appetite"]),
                ("操作倾向", ctx["action_bias"]),
                ("观点强度", ctx["conviction_level"]),
                ("可执行性等级", ctx["actionability_level"]),
            ],
        )

        if ctx["core_targets"]:
            lines.extend(
                [
                    "",
                    "### 核心标的与方向",
                    "",
                    "| 对象 | 类型 | 观点方向 | 时间周期 | 态度 | 当前动作 |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
            for item in ctx["core_targets"]:
                lines.append(
                    f"| {item['target_name']} | {item['target_type']} | {item['direction']} | {item['time_horizon']} | {item['conviction']} | {item['action']} |"
                )

        lines.extend(["", "---"])

        if ctx["core_question"] or ctx["main_argument_chain"] or ctx["key_assumptions"] or ctx["conclusion_rows"]:
            lines.extend(["", "## 3. 视频论证主线", ""])
            if ctx["core_question"]:
                lines.extend(["### 作者试图回答的核心问题", "", ctx["core_question"], ""])
            if ctx["main_argument_chain"]:
                lines.extend(
                    [
                        "### 核心逻辑链",
                        "",
                        ctx["main_argument_chain"],
                        "",
                        "示例结构：",
                        "",
                        "> 宏观变量变化",
                        "> → 市场风格变化",
                        "> → 行业供需或景气变化",
                        "> → 公司收入与利润变化",
                        "> → 估值或市场预期变化",
                        "> → 股价表现。",
                        "",
                    ]
                )
            if ctx["key_assumptions"]:
                lines.extend(["### 关键假设", ""])
                lines.extend(f"* {item}" for item in ctx["key_assumptions"])
                lines.append("")
            if ctx["conclusion_rows"]:
                lines.extend(
                    [
                        "### 核心结论与依据",
                        "",
                        "| 结论 | 分析对象 | 主要依据 | 信息性质 | 证据编号 | 证据质量 |",
                        "| --- | --- | --- | --- | --- | --- |",
                    ]
                )
                for row in ctx["conclusion_rows"]:
                    lines.append(
                        f"| {row['conclusion']} | {row['target']} | {row['reasoning']} | {row['source_status']} | {row['evidence_ids']} | {row['evidence_quality']} |"
                    )
                lines.extend(["", "---"])

        if ctx["macro_section"]:
            lines.extend(["", "## 4. 市场环境与宏观判断", "", "> 仅在视频涉及宏观、大盘或市场风格时展示。", ""])
            market_env = ctx["macro_section"].get("market_environment") or {}
            if market_env:
                lines.extend(["### 市场环境", ""])
                VideoSummaryMarkdownExporter._append_bullets(
                    lines,
                    [
                        ("市场阶段", market_env.get("market_regime")),
                        ("市场趋势", market_env.get("market_trend")),
                        ("市场情绪", market_env.get("market_sentiment")),
                        ("市场风格", market_env.get("market_style")),
                        ("流动性环境", market_env.get("liquidity_condition")),
                        ("成交量状态", market_env.get("turnover_condition")),
                        ("风险偏好", market_env.get("risk_appetite")),
                        ("赚钱效应", market_env.get("profit_effect")),
                        ("亏钱效应", market_env.get("loss_effect")),
                    ],
                )
            macro_rows = ctx["macro_section"].get("macro_rows") or []
            if macro_rows:
                lines.extend(
                    [
                        "",
                        "### 宏观变量",
                        "",
                        "| 宏观变量 | 作者判断 | 变化方向 | 影响对象 | 影响周期 | 证据编号 |",
                        "| --- | --- | --- | --- | --- | --- |",
                    ]
                )
                for row in macro_rows:
                    lines.append(
                        f"| {row['name']} | {row['view']} | {row['direction']} | {row['targets']} | {row['horizon']} | {row['evidence_ids']} |"
                    )
            key_indicators = ctx["macro_section"].get("key_indicators") or []
            if key_indicators:
                lines.extend(["", "### 重点宏观指标", ""])
                lines.extend(f"* {item}" for item in key_indicators)
            market_bullish = ctx["macro_section"].get("market_bullish_triggers") or []
            if market_bullish:
                lines.extend(["", "### 市场转强条件", ""])
                lines.extend(f"* {item}" for item in market_bullish)
            market_invalidations = ctx["macro_section"].get("market_invalidation_conditions") or []
            if market_invalidations:
                lines.extend(["", "### 市场转弱或证伪条件", ""])
                lines.extend(f"* {item}" for item in market_invalidations)
            lines.extend(["", "---"])

        if ctx["themes_detail"]:
            lines.extend(["", "## 5. 行业与主题观点", "", "> 每个行业或主题独立生成一个分析单元。"])
            for item in ctx["themes_detail"]:
                lines.extend(["", f"### {item['name']}", "", "#### 基础判断", ""])
                VideoSummaryMarkdownExporter._append_bullets(
                    lines,
                    [
                        ("类型", item.get("type")),
                        ("观点方向", item.get("direction")),
                        ("观点强度", item.get("conviction")),
                        ("时间周期", item.get("time_horizon")),
                        ("当前阶段", item.get("stage")),
                        ("是否为市场主线", item.get("is_main_theme")),
                        ("市场拥挤度", item.get("crowding_level")),
                        ("市场定价程度", item.get("priced_in_level")),
                        ("预期差方向", item.get("expectation_gap")),
                    ],
                )
                if item.get("thesis"):
                    lines.extend(["", "#### 核心逻辑", "", item["thesis"]])
                if item.get("catalysts"):
                    lines.extend(["", "", "#### 核心催化", ""])
                    lines.extend(f"* {value}" for value in item["catalysts"])
                if item.get("risks"):
                    lines.extend(["", "#### 主要风险", ""])
                    lines.extend(f"* {value}" for value in item["risks"])
                if item.get("evidence_ids"):
                    lines.extend(["", "#### 证据编号", ""])
                    lines.extend(f"* {value}" for value in item["evidence_ids"])
                lines.extend(["", "---"])

        if ctx["symbols_detail"]:
            lines.extend(["", "## 6. 个股与标的观点", "", "> 每个标的独立生成一个分析单元。"])
            for item in ctx["symbols_detail"]:
                lines.extend(["", f"### {item['name']}（{item['code']}）", "", "#### 基础判断", ""])
                VideoSummaryMarkdownExporter._append_bullets(
                    lines,
                    [
                        ("所属市场", item.get("market")),
                        ("所属行业", item.get("industry")),
                        ("观点方向", item.get("symbol_bias")),
                        ("观点强度", item.get("symbol_conviction")),
                        ("时间周期", item.get("symbol_time_horizon")),
                        ("当前动作", item.get("action")),
                        ("关注优先级", item.get("priority")),
                        ("适用价格区间", item.get("applicable_price_range")),
                        ("市场定价程度", item.get("priced_in_level")),
                        ("市场预期差", item.get("expectation_gap")),
                        ("作者是否披露持仓", item.get("position_disclosure")),
                    ],
                )
                if item.get("investment_thesis"):
                    lines.extend(["", "#### 核心投资逻辑", "", item["investment_thesis"]])
                if item.get("bull_points"):
                    lines.extend(["", "", "#### 多头逻辑", ""])
                    lines.extend(f"* {value}" for value in item["bull_points"])
                if item.get("bear_points"):
                    lines.extend(["", "#### 空头逻辑", ""])
                    lines.extend(f"* {value}" for value in item["bear_points"])
                if item.get("catalysts"):
                    lines.extend(["", "#### 核心催化", ""])
                    lines.extend(f"* {value}" for value in item["catalysts"])
                if item.get("risks"):
                    lines.extend(["", "#### 主要风险", ""])
                    lines.extend(f"* {value}" for value in item["risks"])
                if item.get("evidence_ids"):
                    lines.extend(["", "#### 证据编号", ""])
                    lines.extend(f"* {value}" for value in item["evidence_ids"])
                lines.extend(["", "---"])

        if ctx["technical_section"]:
            technical = ctx["technical_section"]
            lines.extend(["", "## 7. 技术分析", "", "> 仅在作者明确进行技术分析时展示。", "", "### 分析对象", ""])
            VideoSummaryMarkdownExporter._append_bullets(
                lines,
                [
                    ("标的或指数", technical.get("technical_target")),
                    ("当前价格或点位", technical.get("current_price")),
                    ("分析周期", technical.get("technical_timeframe")),
                    ("数据截止时间", technical.get("technical_data_time")),
                ],
            )
            if technical.get("technical_summary"):
                lines.extend(["", "### 技术结论", "", technical["technical_summary"]])
            if technical.get("technical_bullish_triggers"):
                lines.extend(["", "", "### 技术面转强条件", ""])
                lines.extend(f"* {item}" for item in technical["technical_bullish_triggers"])
            if technical.get("technical_invalidation_conditions"):
                lines.extend(["", "### 技术面失效条件", ""])
                lines.extend(f"* {item}" for item in technical["technical_invalidation_conditions"])
            lines.extend(["", "---"])

        if ctx["cross_dimension_rows"]:
            lines.extend(
                [
                    "",
                    "## 8. 跨维度综合判断",
                    "",
                    "| 分析维度 | 方向 | 强度 | 时间周期 | 核心依据 |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for row in ctx["cross_dimension_rows"]:
                lines.append(
                    f"| {row['dimension']} | {row['direction']} | {row['strength']} | {row['horizon']} | {row['reason']} |"
                )
            lines.extend(["", "### 综合结论", ""])
            VideoSummaryMarkdownExporter._append_bullets(
                lines,
                [
                    ("长期判断", ctx["cross_dimension_conclusion"].get("long_term_conclusion")),
                    ("中期判断", ctx["cross_dimension_conclusion"].get("medium_term_conclusion")),
                    ("短期判断", ctx["cross_dimension_conclusion"].get("short_term_conclusion")),
                    ("当前主要矛盾", ctx["cross_dimension_conclusion"].get("primary_conflict")),
                    ("当前最大预期差", ctx["cross_dimension_conclusion"].get("primary_expectation_gap")),
                    ("综合操作倾向", ctx["cross_dimension_conclusion"].get("combined_action")),
                ],
            )
            lines.extend(["", "---"])

        if ctx["conflict_section"]:
            lines.extend(["", "## 9. 观点冲突与条件变化", ""])
            for title, values in ctx["conflict_section"].items():
                if not values:
                    continue
                lines.extend([f"### {title}", ""])
                lines.extend(f"* {item}" for item in values)
                lines.append("")
            lines.extend(["---"])

        lines.extend(["", "## 10. 事实、观点、预测与模型推断", ""])
        if ctx["facts_table"]:
            lines.extend(
                [
                    "### 已确认事实",
                    "",
                    "| 事实内容 | 时间 | 涉及对象 | 信息来源 | 核验状态 | 证据编号 |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
            for row in ctx["facts_table"]:
                lines.append(
                    f"| {row['fact']} | {row['fact_time']} | {row['fact_target']} | {row['source_type']} | {row['verification_status']} | {row['evidence_ids']} |"
                )
            lines.append("")
        if ctx["opinions_table"]:
            lines.extend(
                [
                    "### 观点",
                    "",
                    "| 观点内容 | 发言人 | 对象 | 方向 | 强度 | 时间周期 | 证据编号 |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for row in ctx["opinions_table"]:
                lines.append(
                    f"| {row['opinion']} | {row['speaker']} | {row['target']} | {row['direction']} | {row['strength']} | {row['time_horizon']} | {row['evidence_ids']} |"
                )
            lines.append("")
        if ctx["forecasts_table"]:
            lines.extend(
                [
                    "### 预测",
                    "",
                    "| 预测内容 | 对象 | 目标时间 | 目标值或状态 | 前置条件 | 证伪条件 | 证据编号 |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for row in ctx["forecasts_table"]:
                lines.append(
                    f"| {row['forecast']} | {row['target']} | {row['forecast_time']} | {row['forecast_target_value']} | {row['preconditions']} | {row['invalidation_conditions']} | {row['evidence_ids']} |"
                )
            lines.append("")
        if ctx["model_inferences"]:
            lines.extend(["### 模型推断", "", "> 以下内容不是作者明确表达，而是总结模型基于视频上下文进行的归纳或推导。", ""])
            lines.extend(f"* {item}" for item in ctx["model_inferences"])
            lines.append("")
        lines.extend(["---"])

        lines.extend(["", "## 11. 催化、风险与监控", ""])
        if ctx["catalysts_table"]:
            lines.extend(
                [
                    "### 近期催化",
                    "",
                    "| 催化事件 | 预计时间 | 影响对象 | 影响方向 | 重要程度 | 确定性 | 是否已定价 |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for row in ctx["catalysts_table"]:
                lines.append(
                    f"| {row['catalyst']} | {row['expected_time']} | {row['target']} | {row['direction']} | {row['importance']} | {row['certainty']} | {row['priced_in_level']} |"
                )
            lines.append("")
        if ctx["risks_table"]:
            lines.extend(
                [
                    "### 核心风险",
                    "",
                    "| 风险事件 | 影响对象 | 发生概率 | 影响程度 | 监控指标 |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for row in ctx["risks_table"]:
                lines.append(
                    f"| {row['risk']} | {row['target']} | {row['probability']} | {row['severity']} | {row['monitoring_indicator']} |"
                )
            lines.append("")
        if ctx["monitoring_indicators"]:
            lines.extend(["### 需要持续观察的指标", ""])
            lines.extend(f"* {item}" for item in ctx["monitoring_indicators"])
            lines.append("")
        if ctx["mandatory_review_events"]:
            lines.extend(["### 必须重新评估的事件", ""])
            lines.extend(f"* {item}" for item in ctx["mandatory_review_events"])
            lines.append("")
        if ctx["core_invalidation_conditions"]:
            lines.extend(["### 核心证伪条件", ""])
            for index, item in enumerate(ctx["core_invalidation_conditions"][:3], start=1):
                lines.append(f"{index}. {item}")
            lines.append("")
        lines.extend(["---"])

        lines.extend(["", "## 12. 操作观点", "", "### 明确提出的操作", "", ctx["explicit_actionable_view"], ""])
        if ctx["inferred_actionable_view"]:
            lines.extend(["### 模型归纳的操作倾向", "", "> 本部分必须明确标记为模型归纳，不得冒充作者原始建议。", "", ctx["inferred_actionable_view"], ""])
        lines.extend(["### 操作适用范围", ""])
        VideoSummaryMarkdownExporter._append_bullets(
            lines,
            [
                ("适用投资者", ctx["investor_type"]),
                ("风险等级", ctx["risk_level"]),
                ("适用市场", ctx["applicable_market"]),
                ("适用周期", ctx["applicable_horizon"]),
                ("适用价格区间", ctx["applicable_price_range"]),
            ],
        )
        if ctx["trade_plan_rows"]:
            lines.extend(
                [
                    "",
                    "### 交易计划",
                    "",
                    "| 标的 | 操作方向 | 参考区间 | 建议仓位 | 止损或失效位 | 目标位 | 时间周期 | 触发条件 |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for row in ctx["trade_plan_rows"]:
                lines.append(
                    f"| {row['symbol']} | {row['action']} | {row['entry_range']} | {row['position_size']} | {row['stop_or_invalidation']} | {row['target_price']} | {row['time_horizon']} | {row['trigger_condition']} |"
                )
        if ctx["avoid_actions"]:
            lines.extend(["", "### 明确不建议的操作", ""])
            lines.extend(f"* {item}" for item in ctx["avoid_actions"])
        lines.extend(["", "---"])

        if ctx["coverage_section"]:
            lines.extend(["", "## 13. 未覆盖、待核验与识别疑点", ""])
            for title, values in ctx["coverage_section"].items():
                if not values:
                    continue
                lines.extend([f"### {title}", ""])
                if isinstance(values, list):
                    lines.extend(f"* {item}" for item in values)
                else:
                    lines.append(str(values))
                lines.append("")
            lines.extend(["---"])

        if ctx["chapters_detail"]:
            lines.extend(["", "## 14. 章节摘要"])
            for chapter in ctx["chapters_detail"]:
                lines.extend(
                    [
                        "",
                        f"### {chapter['chapter_title']}",
                        "",
                    ]
                )
                VideoSummaryMarkdownExporter._append_bullets(
                    lines,
                    [
                        ("时间范围", chapter.get("time_range")),
                        ("章节类型", chapter.get("chapter_type")),
                        ("章节主题", chapter.get("chapter_topic")),
                        ("核心内容", chapter.get("chapter_summary")),
                        ("涉及市场", VideoSummaryMarkdownExporter._render_inline_list(chapter.get("related_markets") or [])),
                        ("涉及行业", VideoSummaryMarkdownExporter._render_inline_list(chapter.get("related_sectors") or [])),
                        ("涉及标的", VideoSummaryMarkdownExporter._render_inline_list(chapter.get("related_symbols") or [])),
                        ("主要观点", VideoSummaryMarkdownExporter._render_inline_list(chapter.get("chapter_viewpoints") or [])),
                        ("关键数据", VideoSummaryMarkdownExporter._render_inline_list(chapter.get("chapter_data_points") or [])),
                        ("重要性等级", chapter.get("importance_level")),
                        ("证据编号", VideoSummaryMarkdownExporter._render_inline_list(chapter.get("evidence_ids") or [])),
                    ],
                )
            lines.extend(
                [
                    "",
                    "章节类型可选：",
                    "",
                    "* 核心内容",
                    "* 宏观分析",
                    "* 市场复盘",
                    "* 行业分析",
                    "* 个股分析",
                    "* 技术分析",
                    "* 操作建议",
                    "* 风险提示",
                    "* 广告",
                    "* 闲聊",
                    "* 重复内容",
                    "* 无效内容",
                    "",
                    "---",
                ]
            )

        if ctx["evidence_detail"]:
            lines.extend(["", "## 15. 证据片段"])
            for item in ctx["evidence_detail"]:
                lines.extend(["", f"### {item['evidence_id']}", ""])
                VideoSummaryMarkdownExporter._append_bullets(
                    lines,
                    [
                        ("时间范围", item.get("time_range")),
                        ("发言人", item.get("speaker")),
                        ("表达类型", item.get("expression_type")),
                        ("证据模态", item.get("modality")),
                        ("原始内容", item.get("original_content")),
                        ("规范化内容", item.get("normalized_content")),
                        ("证据类型", item.get("evidence_type")),
                        ("涉及对象", VideoSummaryMarkdownExporter._render_inline_list(item.get("related_entities") or [])),
                        ("支撑结论", VideoSummaryMarkdownExporter._render_inline_list(item.get("supported_claim_ids") or [])),
                        ("转录或识别置信度", item.get("recognition_confidence")),
                        ("证据强度", item.get("evidence_strength")),
                    ],
                )
            lines.extend(
                [
                    "",
                    "表达类型可选：",
                    "",
                    "* 本人观点",
                    "* 引用他人观点",
                    "* 市场共识",
                    "* 提问",
                    "* 假设",
                    "* 反驳",
                    "* 历史观点回顾",
                    "* 条件判断",
                    "",
                    "证据模态可选：",
                    "",
                    "* 语音",
                    "* 字幕",
                    "* 屏幕文字",
                    "* K线图",
                    "* 技术指标图",
                    "* 表格",
                    "* PPT",
                    "* 财报截图",
                    "* 研报截图",
                    "* 作者手工标注",
                    "",
                    "---",
                ]
            )

        lines.extend(["", "## 16. 最终评价", "", "### 内容质量", ""])
        VideoSummaryMarkdownExporter._append_bullets(
            lines,
            [
                ("信息密度", ctx["quality"]["information_density"]),
                ("论证完整度", ctx["quality"]["argument_completeness"]),
                ("数据可信度", ctx["quality"]["data_reliability"]),
                ("证据覆盖率", ctx["quality"]["evidence_coverage"]),
                ("可验证程度", ctx["quality"]["verifiability"]),
                ("可操作程度", ctx["quality"]["actionability"]),
                ("时效性", ctx["quality"]["timeliness"]),
                ("独立观点程度", ctx["quality"]["originality"]),
                ("情绪化程度", ctx["quality"]["emotional_level"]),
                ("推广或利益冲突风险", ctx["quality"]["promotional_risk"]),
            ],
        )
        if ctx["content_strengths"]:
            lines.extend(["", "### 主要优点", ""])
            lines.extend(f"* {item}" for item in ctx["content_strengths"])
        if ctx["content_weaknesses"]:
            lines.extend(["", "### 主要缺陷", ""])
            lines.extend(f"* {item}" for item in ctx["content_weaknesses"])
        lines.extend(
            [
                "",
                "### 是否值得纳入知识库",
                "",
                ctx["knowledge_base_decision"],
                "",
                "### 建议纳入知识库的内容",
                "",
            ]
        )
        lines.extend(f"* {item}" for item in ctx["knowledge_items_to_store"])
        if ctx["knowledge_items_to_exclude"]:
            lines.extend(["", "### 不建议直接采用的内容", ""])
            lines.extend(f"* {item}" for item in ctx["knowledge_items_to_exclude"])
        lines.extend(["", "### 建议复核时间", "", f"* {ctx['review_time']}", "", "---"])

        lines.extend(["", "## 17. 时效与冲突处理", ""])
        VideoSummaryMarkdownExporter._append_bullets(
            lines,
            [
                ("视频发布时间", ctx["publish_time"]),
                ("录制时间", ctx["recording_time"]),
                ("数据截止时间", ctx["data_cutoff_time"]),
                ("观点有效期", ctx["valid_until"]),
                ("是否为时间敏感知识", ctx["is_time_sensitive"]),
                ("是否检测到历史观点冲突", ctx["has_historical_conflict"]),
                ("历史观点变化类型", ctx["view_change_type"]),
                ("被替代观点ID", VideoSummaryMarkdownExporter._render_inline_list(ctx["superseded_claim_ids"])),
                ("最新有效观点ID", VideoSummaryMarkdownExporter._render_inline_list(ctx["latest_claim_ids"])),
            ],
        )
        lines.extend(
            [
                "",
                "默认处理规则：",
                "",
                "1. 行情、点位、估值、业绩预测和操作观点优先采用时间更新的内容。",
                "2. 公司历史、商业模式和产业链结构不得仅因发布时间较新而自动覆盖。",
                "3. 新观点必须在证据更充分、前提变化或原观点被证伪时才能替代旧观点。",
                "4. 长期看多和短期看空可以同时成立，不应自动判定为冲突。",
                "5. 看多或看空不得自动转换为买入或卖出建议。",
                "6. 已过有效期的观点不得作为当前操作依据。",
                "",
                "---",
                "",
                "## 18. 生成约束",
                "",
                "1. 视频未提及的字段不得推测补全。",
                "2. 不适用的章节直接隐藏，不输出空章节。",
                "3. 每个核心观点必须关联证据编号。",
                "4. 每个数字必须标注时间、单位、币种和统计口径。",
                "5. 每个技术点位必须绑定分析对象和分析周期。",
                "6. 每个预测必须包含时间范围；无法确认时标记为“时间不明”。",
                "7. 观点、市场共识、引用观点、模型推断和外部核验必须严格分离。",
                "8. 主持人的提问不得识别为嘉宾观点。",
                "9. 引用后进行反驳的内容不得识别为作者认同。",
                "10. 条件未满足时，不得将条件性判断总结为确定性结论。",
                "11. 存在转录或画面疑点的信息不得归类为已确认事实。",
                "12. 模型不得自行生成作者未提供的目标价、仓位、概率和收益率。",
                "13. 重复观点应合并，只保留一条结论并关联多个证据片段。",
                "14. 标题与正文不一致时，以正文中的完整表述为准。",
                "15. 所有操作观点必须说明适用价格、时间周期和失效条件。",
                "16. “估值便宜”“增长较快”“明显强于市场”等比较性结论必须注明比较基准。",
                "17. 事实、观点和预测均应保留原始表述，避免过度改写作者语义。",
                "18. 无法判断时必须输出“未知”“未提及”或“待核验”，不得强行生成结论。",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _append_section(lines: list[str], title: str) -> None:
        lines.extend([title, ""])

    @staticmethod
    def _append_bullets(lines: list[str], items: list[tuple[str, str]]) -> None:
        for label, value in items:
            lines.append(f"* {label}：{value or '未知'}")

    @staticmethod
    def _render_inline_list(items: list[str]) -> str:
        clean = [str(item).strip() for item in items if str(item).strip()]
        return "、".join(clean) if clean else "未知"

    @staticmethod
    def _build_template_context(metadata: dict, summary: dict) -> dict:
        template_context = dict(summary.get("template_context") or {})
        core_summary = str(summary.get("core_summary") or "").strip() or "未提及"
        fact_points = VideoSummaryMarkdownExporter._clean_list(summary.get("fact_points") or [])
        bull_points = VideoSummaryMarkdownExporter._clean_list(summary.get("bull_points") or [])
        bear_points = VideoSummaryMarkdownExporter._clean_list(summary.get("bear_points") or [])
        forecast_points = VideoSummaryMarkdownExporter._clean_list(summary.get("forecast_points") or [])
        invalidations = VideoSummaryMarkdownExporter._clean_list(summary.get("invalidation_conditions") or [])
        catalysts = VideoSummaryMarkdownExporter._clean_list(summary.get("catalysts") or [])
        risks = VideoSummaryMarkdownExporter._clean_list(summary.get("risks") or [])
        themes = VideoSummaryMarkdownExporter._clean_list(summary.get("themes") or [])
        symbols = VideoSummaryMarkdownExporter._clean_list(summary.get("symbols") or [])
        evidence = summary.get("evidence_segments") or []
        chapters = VideoSummaryMarkdownExporter._resolve_section(
            template_context,
            "chapters_detail",
            VideoSummaryMarkdownExporter._build_chapters_detail(summary.get("chapter_summaries") or []),
        )
        market_bias = template_context.get("market_bias") or VideoSummaryMarkdownExporter._derive_market_bias(bull_points, bear_points, core_summary)
        confidence = summary.get("confidence_score")
        confidence_text = str(confidence if confidence is not None else "未知")
        inferred_markets = VideoSummaryMarkdownExporter._infer_markets(core_summary, bull_points, bear_points, themes, symbols, str(summary.get("actionable_view") or ""))
        inferred_sectors = VideoSummaryMarkdownExporter._infer_sectors(core_summary, bull_points, bear_points, themes, symbols)
        quality = {
            "information_density": template_context.get("information_density") or VideoSummaryMarkdownExporter._score_label(len(fact_points) + len(forecast_points) + len(evidence), 12, 6),
            "argument_completeness": template_context.get("argument_completeness") or VideoSummaryMarkdownExporter._score_label(len(chapters), 5, 3),
            "data_reliability": template_context.get("data_reliability") or ("中" if fact_points else "待核验"),
            "evidence_coverage": template_context.get("evidence_coverage") or VideoSummaryMarkdownExporter._score_label(len(evidence), 8, 4),
            "verifiability": template_context.get("verifiability") or ("中高" if fact_points else "中"),
            "actionability": template_context.get("actionability") or ("中" if summary.get("actionable_view") else "低"),
            "timeliness": template_context.get("timeliness") or "高",
            "originality": template_context.get("originality") or ("中" if core_summary else "低"),
            "emotional_level": template_context.get("emotional_level") or "中",
            "promotional_risk": template_context.get("promotional_risk") or "低",
        }
        return {
            "title": str(metadata.get("title") or "视频总结"),
            "video_id": str(metadata.get("id") or metadata.get("bvid") or metadata.get("platform_video_id") or "未知"),
            "author": str(metadata.get("author_name") or "未知"),
            "speakers": VideoSummaryMarkdownExporter._clean_list(template_context.get("speakers") or []),
            "publish_time": str(metadata.get("publish_time") or "未知"),
            "recording_time": str(template_context.get("recording_time") or "未知"),
            "data_cutoff_time": str(template_context.get("data_cutoff_time") or metadata.get("publish_time") or "未知"),
            "duration": VideoSummaryMarkdownExporter._format_duration(metadata.get("duration_seconds")),
            "source_url": str(metadata.get("url") or "未知"),
            "video_type": str(summary.get("video_type") or "GENERAL_FINANCE"),
            "markets": VideoSummaryMarkdownExporter._clean_list(template_context.get("markets") or inferred_markets),
            "sectors": VideoSummaryMarkdownExporter._clean_list(template_context.get("sectors") or inferred_sectors),
            "themes": themes,
            "symbols": symbols,
            "llm_provider": str(summary.get("llm_provider") or "unknown"),
            "llm_model": str(summary.get("llm_model") or "unknown"),
            "confidence_score": confidence_text,
            "timeliness_level": str(template_context.get("timeliness_level") or "高"),
            "valid_until": str(template_context.get("valid_until") or "待人工复核"),
            "one_sentence_summary": str(template_context.get("one_sentence_summary") or VideoSummaryMarkdownExporter._one_sentence(core_summary)),
            "core_summary": core_summary,
            "key_conclusions": VideoSummaryMarkdownExporter._build_key_conclusions(core_summary, bull_points, bear_points, forecast_points),
            "market_bias": market_bias,
            "primary_time_horizon": str(
                template_context.get("primary_time_horizon")
                or VideoSummaryMarkdownExporter._derive_primary_horizon(
                    forecast_points,
                    fallback_text=" ".join([str(summary.get("actionable_view") or ""), core_summary]),
                )
            ),
            "risk_appetite": str(template_context.get("risk_appetite") or VideoSummaryMarkdownExporter._derive_risk_appetite(risks, summary.get("actionable_view"))),
            "action_bias": str(template_context.get("action_bias") or VideoSummaryMarkdownExporter._derive_action_bias(summary.get("actionable_view"))),
            "conviction_level": str(template_context.get("conviction_level") or VideoSummaryMarkdownExporter._derive_conviction_level(confidence)),
            "actionability_level": str(template_context.get("actionability_level") or ("中" if summary.get("actionable_view") else "低")),
            "core_targets": VideoSummaryMarkdownExporter._resolve_section(template_context, "core_targets", []),
            "core_question": str(template_context.get("core_question") or VideoSummaryMarkdownExporter._derive_core_question(metadata)),
            "main_argument_chain": str(template_context.get("main_argument_chain") or VideoSummaryMarkdownExporter._derive_argument_chain(fact_points, forecast_points, core_summary)),
            "key_assumptions": VideoSummaryMarkdownExporter._clean_list(template_context.get("key_assumptions") or invalidations[:3]),
            "conclusion_rows": VideoSummaryMarkdownExporter._resolve_section(
                template_context,
                "conclusion_rows",
                VideoSummaryMarkdownExporter._build_conclusion_rows(core_summary, fact_points, evidence),
            ),
            "macro_section": VideoSummaryMarkdownExporter._resolve_section(
                template_context,
                "macro_section",
                VideoSummaryMarkdownExporter._build_macro_section(fact_points, forecast_points, risks, summary.get("actionable_view")),
            ),
            "themes_detail": VideoSummaryMarkdownExporter._resolve_section(template_context, "themes_detail", []),
            "symbols_detail": VideoSummaryMarkdownExporter._resolve_section(template_context, "symbols_detail", []),
            "technical_section": VideoSummaryMarkdownExporter._resolve_section(template_context, "technical_section", None),
            "cross_dimension_rows": VideoSummaryMarkdownExporter._resolve_section(template_context, "cross_dimension_rows", []),
            "cross_dimension_conclusion": VideoSummaryMarkdownExporter._resolve_section(template_context, "cross_dimension_conclusion", {}),
            "conflict_section": VideoSummaryMarkdownExporter._resolve_section(template_context, "conflict_section", {}),
            "facts_table": VideoSummaryMarkdownExporter._resolve_section(
                template_context,
                "facts_table",
                VideoSummaryMarkdownExporter._build_fact_rows(fact_points, metadata),
            ),
            "opinions_table": VideoSummaryMarkdownExporter._resolve_section(
                template_context,
                "opinions_table",
                VideoSummaryMarkdownExporter._build_opinion_rows(bull_points, bear_points, metadata),
            ),
            "forecasts_table": VideoSummaryMarkdownExporter._resolve_section(
                template_context,
                "forecasts_table",
                VideoSummaryMarkdownExporter._build_forecast_rows(forecast_points, invalidations),
            ),
            "model_inferences": VideoSummaryMarkdownExporter._clean_list(template_context.get("model_inferences") or []),
            "catalysts_table": VideoSummaryMarkdownExporter._resolve_section(
                template_context,
                "catalysts_table",
                VideoSummaryMarkdownExporter._build_catalyst_rows(catalysts),
            ),
            "risks_table": VideoSummaryMarkdownExporter._resolve_section(
                template_context,
                "risks_table",
                VideoSummaryMarkdownExporter._build_risk_rows(risks),
            ),
            "monitoring_indicators": VideoSummaryMarkdownExporter._clean_list(template_context.get("monitoring_indicators") or invalidations[:3]),
            "mandatory_review_events": VideoSummaryMarkdownExporter._clean_list(template_context.get("mandatory_review_events") or []),
            "core_invalidation_conditions": VideoSummaryMarkdownExporter._clean_list(invalidations[:3]),
            "explicit_actionable_view": str(summary.get("actionable_view") or "未明确给出"),
            "inferred_actionable_view": str(VideoSummaryMarkdownExporter._resolve_section(template_context, "inferred_actionable_view", "") or ""),
            "investor_type": str(template_context.get("investor_type") or "普通二级市场投资者"),
            "risk_level": str(template_context.get("risk_level") or "中"),
            "applicable_market": str(
                template_context.get("applicable_market")
                or VideoSummaryMarkdownExporter._render_inline_list(template_context.get("markets") or inferred_markets)
            ),
            "applicable_horizon": str(
                template_context.get("applicable_horizon")
                or VideoSummaryMarkdownExporter._derive_primary_horizon(
                    forecast_points,
                    fallback_text=" ".join([str(summary.get("actionable_view") or ""), core_summary]),
                )
            ),
            "applicable_price_range": str(template_context.get("applicable_price_range") or "未提及"),
            "trade_plan_rows": VideoSummaryMarkdownExporter._resolve_section(template_context, "trade_plan_rows", []),
            "avoid_actions": VideoSummaryMarkdownExporter._clean_list(
                VideoSummaryMarkdownExporter._resolve_section(template_context, "avoid_actions", []) or []
            ),
            "coverage_section": VideoSummaryMarkdownExporter._resolve_section(
                template_context,
                "coverage_section",
                VideoSummaryMarkdownExporter._build_coverage_section(template_context),
            ),
            "chapters_detail": chapters,
            "evidence_detail": VideoSummaryMarkdownExporter._resolve_section(
                template_context,
                "evidence_detail",
                VideoSummaryMarkdownExporter._build_evidence_detail(evidence, metadata),
            ),
            "quality": quality,
            "content_strengths": VideoSummaryMarkdownExporter._clean_list(template_context.get("content_strengths") or VideoSummaryMarkdownExporter._build_strengths(fact_points, evidence, chapters)),
            "content_weaknesses": VideoSummaryMarkdownExporter._clean_list(template_context.get("content_weaknesses") or VideoSummaryMarkdownExporter._build_weaknesses(template_context, evidence)),
            "knowledge_base_decision": str(template_context.get("knowledge_base_decision") or "建议纳入知识库，但保留时效与识别风险标记。"),
            "knowledge_items_to_store": VideoSummaryMarkdownExporter._clean_list(template_context.get("knowledge_items_to_store") or VideoSummaryMarkdownExporter._build_knowledge_items(core_summary, fact_points, forecast_points)),
            "knowledge_items_to_exclude": VideoSummaryMarkdownExporter._clean_list(template_context.get("knowledge_items_to_exclude") or []),
            "review_time": str(template_context.get("review_time") or "建议在下一次同主题视频或关键数据更新后复核"),
            "is_time_sensitive": str(template_context.get("is_time_sensitive") or "是"),
            "has_historical_conflict": str(template_context.get("has_historical_conflict") or "否"),
            "view_change_type": str(template_context.get("view_change_type") or "未检测"),
            "superseded_claim_ids": VideoSummaryMarkdownExporter._clean_list(template_context.get("superseded_claim_ids") or []),
            "latest_claim_ids": VideoSummaryMarkdownExporter._clean_list(template_context.get("latest_claim_ids") or []),
        }

    @staticmethod
    def _clean_list(items: list[str]) -> list[str]:
        return [str(item).strip() for item in items if str(item).strip()]

    @staticmethod
    def _resolve_section(template_context: dict, key: str, fallback: object) -> object:
        return template_context[key] if key in template_context else fallback

    @staticmethod
    def _infer_direction(text: str) -> str:
        value = str(text or "")
        if any(token in value for token in ("上升", "回升", "走高", "增长", "放量", "反弹", "改善", "增加", "扩张", "修复")):
            return "上行/改善"
        if any(token in value for token in ("下滑", "下降", "回落", "收缩", "去杠杆", "负增长", "承压", "走弱", "回调", "缩量")):
            return "下行/收缩"
        return "待核验"

    @staticmethod
    def _format_duration(duration_seconds: object) -> str:
        try:
            total = int(duration_seconds)
        except (TypeError, ValueError):
            return "未知"
        minutes, seconds = divmod(max(total, 0), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _one_sentence(text: str) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return "未提及"
        for token in ("。", "！", "？"):
            if token in normalized:
                return normalized.split(token)[0].strip() + token
        return normalized[:120]

    @staticmethod
    def _build_key_conclusions(core_summary: str, bull_points: list[str], bear_points: list[str], forecast_points: list[str]) -> list[str]:
        items = [VideoSummaryMarkdownExporter._one_sentence(core_summary), *bull_points, *bear_points, *forecast_points]
        deduped: list[str] = []
        for item in items:
            if item and item not in deduped:
                deduped.append(item)
        return deduped[:3] or ["未提及"]

    @staticmethod
    def _derive_market_bias(bull_points: list[str], bear_points: list[str], core_summary: str) -> str:
        if len(bear_points) > len(bull_points):
            return "偏谨慎"
        if len(bull_points) > len(bear_points):
            return "偏积极"
        if any(token in core_summary for token in ("风险", "承压", "回调", "去杠杆")):
            return "中性偏谨慎"
        return "中性"

    @staticmethod
    def _derive_primary_horizon(forecast_points: list[str], fallback_text: str = "") -> str:
        text = " ".join([*forecast_points, fallback_text])
        if any(token in text for token in ("下周", "明天", "短期", "近期")):
            return "短期"
        if any(token in text for token in ("季度", "下半年", "中期")):
            return "中期"
        if any(token in text for token in ("长期", "中长期")):
            return "中长期"
        return "时间不明"

    @staticmethod
    def _infer_markets(
        core_summary: str,
        bull_points: list[str],
        bear_points: list[str],
        themes: list[str],
        symbols: list[str],
        actionable_view: str,
    ) -> list[str]:
        text = " ".join([core_summary, actionable_view, *bull_points, *bear_points, *themes, *symbols])
        mapping = (
            ("A股", ("A股", "上证", "沪深", "中证", "微盘股", "两融")),
            ("美股", ("美股", "纳指", "标普", "道指", "美国")),
            ("港股", ("港股", "恒生", "恒指")),
            ("韩国股市", ("韩国", "KOSPI")),
            ("黄金", ("黄金", "COMEX黄金")),
            ("原油", ("原油", "油价")),
        )
        markets = [label for label, keywords in mapping if any(keyword in text for keyword in keywords)]
        return markets or []

    @staticmethod
    def _infer_sectors(
        core_summary: str,
        bull_points: list[str],
        bear_points: list[str],
        themes: list[str],
        symbols: list[str],
    ) -> list[str]:
        text = " ".join([core_summary, *bull_points, *bear_points, *themes, *symbols])
        mapping = (
            ("医药", ("医药", "CXO", "药明康德", "凯莱英")),
            ("半导体", ("半导体", "芯片", "存储")),
            ("科技", ("科技", "成长股", "AI")),
            ("证券", ("证券", "券商")),
            ("地产", ("地产", "房地产")),
            ("消费", ("消费", "社消", "618")),
        )
        sectors = [label for label, keywords in mapping if any(keyword in text for keyword in keywords)]
        return sectors or []

    @staticmethod
    def _derive_risk_appetite(risks: list[str], actionable_view: object) -> str:
        text = " ".join(risks) + " " + str(actionable_view or "")
        if any(token in text for token in ("谨慎", "等待", "回避", "风险")):
            return "偏低"
        if any(token in text for token in ("进攻", "加仓", "积极")):
            return "偏高"
        return "中性"

    @staticmethod
    def _derive_action_bias(actionable_view: object) -> str:
        text = str(actionable_view or "")
        if any(token in text for token in ("观望", "等待", "谨慎")):
            return "等待确认"
        if any(token in text for token in ("低吸", "布局", "关注")):
            return "逢低关注"
        if any(token in text for token in ("减仓", "回避")):
            return "风险控制优先"
        return "未提及"

    @staticmethod
    def _derive_conviction_level(confidence: object) -> str:
        try:
            score = float(confidence)
        except (TypeError, ValueError):
            return "中"
        if score >= 0.8:
            return "高"
        if score >= 0.6:
            return "中"
        return "低"

    @staticmethod
    def _build_core_targets(symbols: list[str], market_bias: str, actionable_view: object) -> list[dict]:
        action = str(actionable_view or "待核验")
        rows = []
        for symbol in symbols[:5]:
            rows.append(
                {
                    "target_name": symbol,
                    "target_type": "标的/主题",
                    "direction": market_bias,
                    "time_horizon": "中短期",
                    "conviction": "中",
                    "action": action or "待核验",
                }
            )
        return rows

    @staticmethod
    def _derive_core_question(metadata: dict) -> str:
        title = str(metadata.get("title") or "").strip()
        if not title:
            return "作者试图解释当前市场与相关资产的核心变化。"
        return f"{title} 这一主题下，作者试图回答当前市场阶段应如何理解与应对。"

    @staticmethod
    def _derive_argument_chain(fact_points: list[str], forecast_points: list[str], core_summary: str) -> str:
        chain_parts = []
        if fact_points:
            chain_parts.append(VideoSummaryMarkdownExporter._one_sentence(fact_points[0]))
        if len(fact_points) > 1:
            chain_parts.append(VideoSummaryMarkdownExporter._one_sentence(fact_points[1]))
        if forecast_points:
            chain_parts.append(VideoSummaryMarkdownExporter._one_sentence(forecast_points[0]))
        if not chain_parts:
            chain_parts.append(VideoSummaryMarkdownExporter._one_sentence(core_summary))
        return "\n→ ".join(chain_parts)

    @staticmethod
    def _build_conclusion_rows(core_summary: str, fact_points: list[str], evidence: list[dict]) -> list[dict]:
        conclusions = [VideoSummaryMarkdownExporter._one_sentence(core_summary), *(fact_points[:2] or [])]
        rows = []
        for index, item in enumerate(conclusions[:3], start=1):
            rows.append(
                {
                    "conclusion": item,
                    "target": "视频主线",
                    "reasoning": fact_points[index - 1] if index - 1 < len(fact_points) else core_summary[:80],
                    "source_status": "【上下文归纳】" if index == 1 else "【明确表述】",
                    "evidence_ids": f"E{index}" if evidence else "待补充",
                    "evidence_quality": "中",
                }
            )
        return rows

    @staticmethod
    def _build_macro_section(fact_points: list[str], forecast_points: list[str], risks: list[str], actionable_view: object) -> dict | None:
        merged = " ".join([*fact_points, *forecast_points, *risks, str(actionable_view or "")])
        macro_keywords = (
            "GDP",
            "CPI",
            "PPI",
            "PMI",
            "出口",
            "社消",
            "消费",
            "投资",
            "就业",
            "油价",
            "通胀",
            "流动性",
            "失业率",
            "社融",
            "信贷",
            "融资",
            "两融",
            "利率",
            "汇率",
        )
        if not any(keyword in merged for keyword in macro_keywords):
            return None
        all_points = [*fact_points, *forecast_points]
        rows = []
        for name, keywords in (
            ("经济增长", ("GDP", "增长", "出口", "消费", "投资", "地产")),
            ("通胀", ("CPI", "PPI", "通胀", "油价")),
            ("利率", ("利率", "加息", "降息")),
            ("流动性", ("流动性", "去杠杆", "融资", "两融", "社融", "信贷")),
            ("大宗商品", ("油价", "黄金", "原油", "铜", "商品")),
        ):
            matched = next((item for item in all_points if any(keyword in item for keyword in keywords)), "")
            if not matched:
                continue
            rows.append(
                {
                    "name": name,
                    "view": VideoSummaryMarkdownExporter._one_sentence(matched),
                    "direction": VideoSummaryMarkdownExporter._infer_direction(matched),
                    "targets": "市场整体",
                    "horizon": "中短期",
                    "evidence_ids": f"E{min(len(rows) + 1, max(len(all_points), 1))}",
                }
            )
        if not rows:
            return None
        liquidity_condition = "两融与杠杆资金收缩" if any(token in merged for token in ("融资", "两融", "去杠杆", "社融", "信贷")) else "待核验"
        market_style = "大盘承压，小盘与防御方向相对占优" if any(token in merged for token in ("微盘", "小市值", "大市值", "风格")) else "结构分化"
        return {
            "market_environment": {
                "market_regime": "去杠杆与结构分化阶段" if "去杠杆" in merged else "结构分化",
                "market_trend": "指数承压、结构分化" if any(token in merged for token in ("承压", "回调", "分化")) else "震荡",
                "market_sentiment": "谨慎" if any(token in merged for token in ("风险", "谨慎", "承压", "去杠杆")) else "中性",
                "market_style": market_style,
                "liquidity_condition": liquidity_condition,
                "turnover_condition": "待核验",
                "risk_appetite": VideoSummaryMarkdownExporter._derive_risk_appetite(risks, actionable_view),
                "profit_effect": "结构性",
                "loss_effect": "高拥挤方向承压" if "去杠杆" in merged else "局部承压",
            },
            "macro_rows": rows,
            "key_indicators": [row["view"] for row in rows[:5]],
            "market_bullish_triggers": [item for item in forecast_points if any(token in item for token in ("回升", "改善", "修复", "反弹", "突破"))][:3],
            "market_invalidation_conditions": [item for item in risks if any(token in item for token in ("加息", "去杠杆", "下滑", "回落", "风险"))][:3],
        }

    @staticmethod
    def _build_themes_detail(themes: list[str], catalysts: list[str], risks: list[str], market_bias: str) -> list[dict]:
        items = []
        for theme in themes[:6]:
            items.append(
                {
                    "name": theme,
                    "type": "主题",
                    "direction": market_bias,
                    "conviction": "中",
                    "time_horizon": "中短期",
                    "stage": "跟踪中",
                    "is_main_theme": "待核验",
                    "crowding_level": "待核验",
                    "priced_in_level": "待核验",
                    "expectation_gap": "待核验",
                    "thesis": f"围绕“{theme}”形成了视频中的重点讨论方向。",
                    "catalysts": catalysts[:2],
                    "risks": risks[:2],
                    "evidence_ids": ["E1"],
                }
            )
        return items

    @staticmethod
    def _build_symbols_detail(
        symbols: list[str],
        bull_points: list[str],
        bear_points: list[str],
        catalysts: list[str],
        risks: list[str],
        actionable_view: object,
    ) -> list[dict]:
        items = []
        for symbol in symbols[:6]:
            items.append(
                {
                    "name": symbol,
                    "code": symbol,
                    "market": "待核验",
                    "industry": "待核验",
                    "symbol_bias": "中性",
                    "symbol_conviction": "中",
                    "symbol_time_horizon": "中短期",
                    "action": str(actionable_view or "待核验"),
                    "priority": "中",
                    "applicable_price_range": "未提及",
                    "priced_in_level": "待核验",
                    "expectation_gap": "待核验",
                    "position_disclosure": "未提及",
                    "investment_thesis": f"视频将 {symbol} 纳入观察范围，但需要结合原视频证据继续复核。",
                    "bull_points": bull_points[:2],
                    "bear_points": bear_points[:2],
                    "catalysts": catalysts[:2],
                    "risks": risks[:2],
                    "evidence_ids": ["E1"],
                }
            )
        return items

    @staticmethod
    def _build_technical_section(summary: dict, metadata: dict) -> dict | None:
        if summary.get("video_type") != "EQUITY_TECHNICAL_ANALYSIS":
            return None
        return {
            "technical_target": str(metadata.get("title") or "未提及"),
            "current_price": "未提及",
            "technical_timeframe": "未提及",
            "technical_data_time": str(metadata.get("publish_time") or "未知"),
            "technical_summary": str(summary.get("core_summary") or "未提及"),
            "technical_bullish_triggers": VideoSummaryMarkdownExporter._clean_list(summary.get("bull_points") or [])[:2],
            "technical_invalidation_conditions": VideoSummaryMarkdownExporter._clean_list(summary.get("invalidation_conditions") or [])[:2],
        }

    @staticmethod
    def _build_cross_dimension_rows(core_summary: str, market_bias: str, risks: list[str], actionable_view: object) -> list[dict]:
        return [
            {
                "dimension": "市场环境",
                "direction": market_bias,
                "strength": "中",
                "horizon": "中短期",
                "reason": VideoSummaryMarkdownExporter._one_sentence(core_summary),
            },
            {
                "dimension": "风险控制",
                "direction": "偏谨慎" if risks else "中性",
                "strength": "中",
                "horizon": "短期",
                "reason": risks[0] if risks else str(actionable_view or "未提及"),
            },
        ]

    @staticmethod
    def _build_fact_rows(fact_points: list[str], metadata: dict) -> list[dict]:
        return [
            {
                "fact": item,
                "fact_time": str(metadata.get("publish_time") or "时间不明"),
                "fact_target": "视频主线",
                "source_type": "视频总结",
                "verification_status": "待核验",
                "evidence_ids": f"E{index}",
            }
            for index, item in enumerate(fact_points[:8], start=1)
        ]

    @staticmethod
    def _build_opinion_rows(bull_points: list[str], bear_points: list[str], metadata: dict) -> list[dict]:
        rows = []
        for index, item in enumerate(bull_points[:4], start=1):
            rows.append(
                {
                    "opinion": item,
                    "speaker": str(metadata.get("author_name") or "作者"),
                    "target": "相关市场/主题",
                    "direction": "偏多",
                    "strength": "中",
                    "time_horizon": "中短期",
                    "evidence_ids": f"E{index}",
                }
            )
        for offset, item in enumerate(bear_points[:4], start=len(rows) + 1):
            rows.append(
                {
                    "opinion": item,
                    "speaker": str(metadata.get("author_name") or "作者"),
                    "target": "相关市场/主题",
                    "direction": "偏空",
                    "strength": "中",
                    "time_horizon": "中短期",
                    "evidence_ids": f"E{offset}",
                }
            )
        return rows

    @staticmethod
    def _build_forecast_rows(forecast_points: list[str], invalidations: list[str]) -> list[dict]:
        return [
            {
                "forecast": item,
                "target": "视频主线",
                "forecast_time": "时间不明",
                "forecast_target_value": "状态判断",
                "preconditions": "待核验",
                "invalidation_conditions": invalidations[index - 1] if index - 1 < len(invalidations) else "待核验",
                "evidence_ids": f"E{index}",
            }
            for index, item in enumerate(forecast_points[:8], start=1)
        ]

    @staticmethod
    def _build_catalyst_rows(catalysts: list[str]) -> list[dict]:
        return [
            {
                "catalyst": item,
                "expected_time": "时间不明",
                "target": "相关市场/主题",
                "direction": "待核验",
                "importance": "中",
                "certainty": "中",
                "priced_in_level": "待核验",
            }
            for item in catalysts[:6]
        ]

    @staticmethod
    def _build_risk_rows(risks: list[str]) -> list[dict]:
        return [
            {
                "risk": item,
                "target": "相关市场/主题",
                "probability": "待核验",
                "severity": "中",
                "monitoring_indicator": "对应证伪条件与后续视频更新",
            }
            for item in risks[:6]
        ]

    @staticmethod
    def _build_trade_plan_rows(symbols: list[str], actionable_view: object, invalidations: list[str]) -> list[dict]:
        rows = []
        for symbol in symbols[:4]:
            rows.append(
                {
                    "symbol": symbol,
                    "action": str(actionable_view or "待核验"),
                    "entry_range": "未提及",
                    "position_size": "未提及",
                    "stop_or_invalidation": invalidations[0] if invalidations else "未提及",
                    "target_price": "未提及",
                    "time_horizon": "中短期",
                    "trigger_condition": "需结合后续市场确认",
                }
            )
        return rows

    @staticmethod
    def _build_coverage_section(template_context: dict) -> dict:
        section = {}
        if template_context.get("important_omissions"):
            section["作者未覆盖的重要问题"] = VideoSummaryMarkdownExporter._clean_list(template_context.get("important_omissions") or [])
        if template_context.get("items_to_verify"):
            section["待核验数据"] = VideoSummaryMarkdownExporter._clean_list(template_context.get("items_to_verify") or [])
        if template_context.get("uncertain_items"):
            section["无法确定的信息"] = VideoSummaryMarkdownExporter._clean_list(template_context.get("uncertain_items") or [])
        if template_context.get("recognition_issues"):
            section["转录或画面识别疑点"] = VideoSummaryMarkdownExporter._clean_list(template_context.get("recognition_issues") or [])
        if template_context.get("title_content_consistency"):
            section["标题与正文一致性"] = [
                f"标题倾向：{template_context.get('title_bias') or '未知'}",
                f"正文倾向：{template_context.get('content_bias') or '未知'}",
                f"一致性：{template_context.get('title_content_consistency') or '未知'}",
                f"是否存在夸张表达：{template_context.get('clickbait_risk') or '未知'}",
            ]
        if template_context.get("context_completeness"):
            section["上下文完整性"] = [
                f"是否为完整视频：{template_context.get('is_full_video') or '未知'}",
                f"是否为二次剪辑：{template_context.get('is_clipped') or '未知'}",
                f"是否为转载：{template_context.get('is_reuploaded') or '未知'}",
                f"上下文完整度：{template_context.get('context_completeness') or '未知'}",
            ]
        return section

    @staticmethod
    def _build_chapters_detail(chapter_summaries: list[str]) -> list[dict]:
        details = []
        for index, raw in enumerate(chapter_summaries, start=1):
            text = str(raw).strip()
            if not text:
                continue
            time_match = re.search(r"\|\s*(\d+)-(\d+)\s*ms\]", text)
            topic_match = re.search(r"主题：(.+)", text)
            summary_match = re.search(r"口播：(.+)", text)
            ocr_match = re.search(r"OCR：(.+)", text)
            details.append(
                {
                    "chapter_title": f"章节 {index}",
                    "time_range": f"{time_match.group(1)} - {time_match.group(2)} ms" if time_match else "时间不明",
                    "chapter_type": "核心内容",
                    "chapter_topic": topic_match.group(1).strip() if topic_match else "未分类",
                    "chapter_summary": summary_match.group(1).strip()[:220] if summary_match else text[:220],
                    "related_markets": [],
                    "related_sectors": [],
                    "related_symbols": [],
                    "chapter_viewpoints": [summary_match.group(1).strip()[:100]] if summary_match else [],
                    "chapter_data_points": [ocr_match.group(1).strip()[:120]] if ocr_match else [],
                    "importance_level": "中",
                    "evidence_ids": [f"E{index}"],
                }
            )
        return details

    @staticmethod
    def _build_evidence_detail(evidence: list[dict], metadata: dict) -> list[dict]:
        details = []
        author = str(metadata.get("author_name") or "作者")
        for index, item in enumerate(evidence, start=1):
            if not isinstance(item, dict):
                details.append(
                    {
                        "evidence_id": f"E{index}",
                        "time_range": "时间不明",
                        "speaker": author,
                        "expression_type": "本人观点",
                        "modality": "语音",
                        "original_content": str(item),
                        "normalized_content": str(item),
                        "evidence_type": "文本",
                        "related_entities": [],
                        "supported_claim_ids": [f"C{index}"],
                        "recognition_confidence": "未知",
                        "evidence_strength": "中",
                    }
                )
                continue
            modality = "表格" if item.get("type") == "visual" and item.get("ocr_text") else ("屏幕文字" if item.get("type") == "visual" else "语音")
            original = str(item.get("text") or item.get("visual_summary") or "").strip()
            details.append(
                {
                    "evidence_id": f"E{index}",
                    "time_range": f"{item.get('start_ms', 0)} - {item.get('end_ms', 0)} ms",
                    "speaker": author,
                    "expression_type": "本人观点" if item.get("type") != "visual" else "市场共识",
                    "modality": modality,
                    "original_content": original or "未提取",
                    "normalized_content": str(item.get("visual_summary") or item.get("text") or "").strip() or "未提取",
                    "evidence_type": "视觉证据" if item.get("type") == "visual" else "语音证据",
                    "related_entities": [],
                    "supported_claim_ids": [f"C{index}"],
                    "recognition_confidence": "中" if item.get("ocr_text") or item.get("text") else "未知",
                    "evidence_strength": "中",
                }
            )
        return details

    @staticmethod
    def _build_strengths(fact_points: list[str], evidence: list[dict], chapters: list[dict]) -> list[str]:
        strengths = []
        if fact_points:
            strengths.append("包含可直接复核的事实提要。")
        if evidence:
            strengths.append("保留了证据片段，便于回溯原视频。")
        if chapters:
            strengths.append("章节拆分较清晰，便于定位核心内容。")
        return strengths or ["结构化摘要完整度尚可。"]

    @staticmethod
    def _build_weaknesses(template_context: dict, evidence: list[dict]) -> list[str]:
        weaknesses = []
        if template_context.get("recognition_issues"):
            weaknesses.append("存在转录或识别疑点，需人工复核。")
        if len(evidence) < 3:
            weaknesses.append("证据片段覆盖仍有限。")
        return weaknesses or ["部分结论仍依赖模型归纳，需结合原视频复核。"]

    @staticmethod
    def _build_knowledge_items(core_summary: str, fact_points: list[str], forecast_points: list[str]) -> list[str]:
        items = [VideoSummaryMarkdownExporter._one_sentence(core_summary), *fact_points[:3], *forecast_points[:2]]
        deduped: list[str] = []
        for item in items:
            if item and item not in deduped:
                deduped.append(item)
        return deduped[:6] or ["待核验"]

    @staticmethod
    def _score_label(value: int, high: int, medium: int) -> str:
        if value >= high:
            return "高"
        if value >= medium:
            return "中"
        return "低"

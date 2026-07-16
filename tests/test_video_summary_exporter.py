from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from engines.content.video_summary_exporter import VideoSummaryMarkdownExporter


def test_video_summary_exporter_writes_markdown():
    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="summary-exporter-test-", dir=temp_root))
    exporter = VideoSummaryMarkdownExporter(export_root=tmp_path)
    metadata = {
        "title": "测试视频总结",
        "author_name": "测试作者",
        "publish_time": "20260710",
        "url": "https://www.bilibili.com/video/BVTEST123",
        "bvid": "BVTEST123",
    }
    summary = {
        "core_summary": "核心观点",
        "bull_points": ["看多点"],
        "bear_points": ["看空点"],
        "themes": ["半导体"],
        "symbols": ["688256"],
        "catalysts": ["业绩催化"],
        "risks": ["估值风险"],
        "fact_points": ["公布了新的业绩指引"],
        "forecast_points": ["下周有望延续修复"],
        "invalidation_conditions": ["跌破关键支撑则失效"],
        "video_type": "EQUITY_TECHNICAL_ANALYSIS",
        "chapter_summaries": ["[章节 1] 半导体与指数共振"],
        "actionable_view": "回调观察",
        "evidence_segments": [
            {"start_ms": 0, "end_ms": 1000, "text": "测试证据"},
            {"type": "visual", "start_ms": 1200, "text": "画面显示均线压制", "ocr_text": "上证指数 MA5 MA10", "image_path": "frame_1.jpg"},
        ],
        "confidence_score": 0.82,
        "llm_provider": "deepseek",
        "llm_model": "deepseek-v4-pro",
    }

    try:
        path = exporter.export(metadata, summary)
        content = path.read_text(encoding="utf-8")
        assert path.exists()
        assert "测试视频总结" in content
        assert "## 1. 视频元信息" in content
        assert "## 2. 核心摘要" in content
        assert "## 18. 生成约束" in content
        assert "测试证据" in content
        assert "画面显示均线压制" in content
        assert "EQUITY_TECHNICAL_ANALYSIS" in content
        assert "跌破关键支撑则失效" in content
        assert "### E1" in content
        assert "### E2" in content
        assert "### 作者观点" not in content
        assert "### 作者预测" not in content
        assert "作者观点强度" not in content
        assert "作者明确提出的操作" not in content
        assert "## 5. 行业与主题观点" not in content
        assert "## 6. 个股与标的观点" not in content
        assert "## 7. 技术分析" not in content
        assert "## 8. 跨维度综合判断" not in content
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

from __future__ import annotations

from engines.content.chapter_segmenter import ChapterSegmenter


def _window(text: str, ocr_text: str = "", visual_summary: str = "", start_ms: int = 0, end_ms: int = 60_000) -> dict:
    return {
        "window_index": 0,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "transcript_text": text,
        "ocr_text": ocr_text,
        "visual_summary": visual_summary,
        "confidence_score": 0.8,
        "entities": [],
    }


def test_chapter_entities_ignore_ocr_sidebar_announcement_codes():
    segmenter = ChapterSegmenter()
    windows = [
        _window(
            "半导体板块已经接近出清的尾声，流动性修复是关键。",
            ocr_text="锦龙股份（000712）：关于股东股份司法拍卖的进展公告 贤丰控股（002141）：股票交易异常波动公告",
        )
    ]

    chapters = segmenter.segment(windows)

    assert len(chapters) == 1
    chapter = chapters[0]
    assert "000712" not in chapter["entities"]
    assert "002141" not in chapter["entities"]
    assert "000712" not in chapter["title"]
    assert "半导体" in chapter["entities"]
    assert chapter["title"].startswith("半导体")


def test_chapter_entities_keep_visual_summary_confirmed_codes():
    segmenter = ChapterSegmenter()
    windows = [
        _window(
            "这个票的走势我们再看看。",
            visual_summary="行情软件主图展示华虹宏力（688347）日K线，股价回踩均线。",
        )
    ]

    chapters = segmenter.segment(windows)

    assert "688347" in chapters[0]["entities"]


def test_chapter_title_prefers_spoken_entity_over_ocr_sidebar_code():
    segmenter = ChapterSegmenter()
    windows = [
        _window(
            "黄金的中期逻辑没有变化，回调是机会。",
            ocr_text="锦龙股份（000712）：关于股东股份司法拍卖的进展公告",
        )
    ]

    chapters = segmenter.segment(windows)

    assert chapters[0]["title"] == "黄金相关分析"


def test_window_entities_ignore_raw_ocr_sidebar_codes():
    from engines.content.temporal_window_builder import TemporalWindowBuilder

    builder = TemporalWindowBuilder()
    transcript = {
        "segments": [
            {"text": "半导体已经接近出清的尾声。", "start_ms": 0, "end_ms": 5000},
        ]
    }
    frame_insights = [
        {
            "timestamp_ms": 2000,
            "ocr_text": "锦龙股份（000712）：关于股东股份司法拍卖的进展公告",
            "visual_summary": "主图展示半导体板块指数（880491）日K线。",
            "symbols": [],
        }
    ]

    windows = builder.build(transcript=transcript, frame_insights=frame_insights)

    assert windows[0]["entities"] == ["880491"]


def test_chapter_title_prefers_readable_entity_over_bare_code():
    segmenter = ChapterSegmenter()
    windows = [
        _window(
            "今天市场缩量调整，盘面比较弱。",
            visual_summary="行情软件展示 999999 上证指数分时图，指数午后震荡。",
        )
    ]

    chapters = segmenter.segment(windows)

    assert chapters[0]["title"] == "上证指数相关分析"


def test_window_entities_skip_frames_judged_not_narration_aligned():
    from engines.content.temporal_window_builder import TemporalWindowBuilder

    builder = TemporalWindowBuilder()
    transcript = {
        "segments": [
            {"text": "半导体已经接近出清的尾声。", "start_ms": 0, "end_ms": 5000},
        ]
    }
    frame_insights = [
        {
            "timestamp_ms": 2000,
            "narration_aligned": False,
            "visual_summary": "画面与口播内容无关，主屏为公告列表，含锦龙股份（000712）。",
            "symbols": ["000712"],
        }
    ]

    windows = builder.build(transcript=transcript, frame_insights=frame_insights)

    assert windows[0]["entities"] == []

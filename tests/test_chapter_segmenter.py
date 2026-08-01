from engines.content.chapter_segmenter import ChapterSegmenter


def _window(index: int, start_seconds: int, end_seconds: int) -> dict:
    return {
        "window_index": index,
        "start_ms": start_seconds * 1000,
        "end_ms": end_seconds * 1000,
        "transcript_text": "市场维持震荡，关注低估值方向。",
        "ocr_text": "",
        "visual_summary": "",
        "entities": [],
    }


def test_long_single_topic_is_split_at_maximum_chapter_duration(monkeypatch):
    monkeypatch.delenv("VIDEO_KNOWLEDGE_CHAPTER_MAX_SECONDS", raising=False)
    segmenter = ChapterSegmenter(max_chapter_seconds=240, max_chapter_chars=10000)

    chapters = segmenter.segment(
        [_window(0, 0, 120), _window(1, 120, 240), _window(2, 240, 360)]
    )

    assert len(chapters) == 2
    assert chapters[0]["boundary_source"] == "MAX_CHAPTER_SIZE"
    assert chapters[0]["end_ms"] == 240000
    assert chapters[1]["start_ms"] == 240000

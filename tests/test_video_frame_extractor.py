from engines.content.video_frame_extractor import VideoFrameExtractor


def test_frame_budget_scales_with_duration_and_cues():
    extractor = VideoFrameExtractor(
        min_frames=8,
        max_frames_cap=48,
        target_seconds_per_frame=75,
        cue_keywords=("这里", "锐捷网络"),
    )
    transcript_segments = [
        {"text": "这里重点看锐捷网络", "start_ms": 10_000, "end_ms": 16_000},
        {"text": "这里再看一下图上", "start_ms": 60_000, "end_ms": 66_000},
        {"text": "锐捷网络这一页很关键", "start_ms": 120_000, "end_ms": 126_000},
    ]

    budget = extractor._resolve_frame_budget(duration_ms=30 * 60 * 1000, transcript_segments=transcript_segments)

    assert budget > 18
    assert budget <= 48


def test_frame_budget_has_reasonable_floor_for_short_video():
    extractor = VideoFrameExtractor(
        min_frames=8,
        max_frames_cap=48,
        target_seconds_per_frame=75,
    )

    budget = extractor._resolve_frame_budget(duration_ms=3 * 60 * 1000, transcript_segments=[])

    assert budget == 8


def test_select_timestamps_preserves_late_visual_cues_for_long_video():
    extractor = VideoFrameExtractor(
        frame_interval_seconds=15,
        min_frames=8,
        max_frames_cap=18,
        target_seconds_per_frame=75,
        cue_keywords=("图上", "利润", "订单"),
    )
    extractor._detect_scene_change_timestamps = lambda video_path, limit: [15_000, 45_000, 60_000, 90_000]
    transcript_segments = [
        {"text": "图上这里先看一下", "start_ms": 30_000, "end_ms": 36_000},
        {"text": "这里的利润和订单要注意", "start_ms": 1_154_000, "end_ms": 1_160_000},
    ]

    timestamps = extractor._select_timestamps(
        video_path=__file__,  # only used by the patched scene detector
        duration_ms=26 * 60 * 1000,
        transcript_segments=transcript_segments,
        frame_budget=extractor._resolve_frame_budget(26 * 60 * 1000, transcript_segments),
    )

    assert any(abs(timestamp - 1_154_000) <= 6_000 for timestamp in timestamps)
    assert max(timestamps) > 1_400_000


def _chapter(start_ms: int, end_ms: int) -> dict:
    return {"start_ms": start_ms, "end_ms": end_ms}


def test_select_timestamps_by_chapter_covers_every_chapter_within_budget():
    extractor = VideoFrameExtractor(
        min_frames=8,
        max_frames_cap=48,
        target_seconds_per_frame=75,
        cue_keywords=("图上",),
    )
    extractor._detect_scene_change_timestamps = lambda video_path, limit: []
    chapters = [_chapter(0, 300_000), _chapter(300_000, 900_000), _chapter(900_000, 1_200_000)]
    budget = 12

    timestamps = extractor._select_timestamps(
        video_path=__file__,
        duration_ms=1_200_000,
        transcript_segments=[],
        frame_budget=budget,
        chapters=chapters,
    )

    assert len(timestamps) <= budget
    for chapter in chapters:
        assert any(chapter["start_ms"] <= timestamp <= chapter["end_ms"] for timestamp in timestamps)


def test_select_timestamps_by_chapter_prefers_cues_inside_chapter():
    extractor = VideoFrameExtractor(
        min_frames=8,
        max_frames_cap=48,
        target_seconds_per_frame=75,
        cue_keywords=("图上",),
        cue_window_seconds=3,
    )
    extractor._detect_scene_change_timestamps = lambda video_path, limit: []
    chapters = [_chapter(0, 600_000)]
    transcript_segments = [{"text": "看图上的这个位置", "start_ms": 250_000, "end_ms": 256_000}]

    timestamps = extractor._select_timestamps(
        video_path=__file__,
        duration_ms=600_000,
        transcript_segments=transcript_segments,
        frame_budget=8,
        chapters=chapters,
    )

    assert any(abs(timestamp - 250_000) <= 6_000 for timestamp in timestamps)


def test_select_timestamps_by_chapter_allocates_proportionally_to_duration():
    quotas = VideoFrameExtractor._allocate_chapter_quotas(
        [_chapter(0, 100_000), _chapter(100_000, 500_000)],
        frame_budget=10,
    )

    assert sum(quotas) == 10
    assert all(quota >= 1 for quota in quotas)
    assert quotas[1] > quotas[0]


def test_select_timestamps_by_chapter_falls_back_when_chapters_empty():
    extractor = VideoFrameExtractor(
        frame_interval_seconds=15,
        min_frames=8,
        max_frames_cap=48,
        target_seconds_per_frame=75,
        cue_keywords=("图上",),
    )
    extractor._detect_scene_change_timestamps = lambda video_path, limit: []

    timestamps = extractor._select_timestamps(
        video_path=__file__,
        duration_ms=600_000,
        transcript_segments=[],
        frame_budget=8,
        chapters=[],
    )

    assert len(timestamps) == 8


def test_select_timestamps_by_chapter_handles_budget_smaller_than_chapter_count():
    quotas = VideoFrameExtractor._allocate_chapter_quotas(
        [_chapter(0, 100_000), _chapter(100_000, 200_000), _chapter(200_000, 300_000)],
        frame_budget=2,
    )

    assert sum(quotas) == 2

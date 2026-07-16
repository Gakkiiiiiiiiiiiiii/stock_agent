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

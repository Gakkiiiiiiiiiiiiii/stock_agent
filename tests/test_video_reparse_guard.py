import pytest

from engines.content.video_ingest_service import VideoIngestService


def test_sparse_reparse_is_rejected_before_it_can_replace_existing_knowledge():
    with pytest.raises(RuntimeError, match="已保留原有知识"):
        VideoIngestService._ensure_reparse_does_not_regress(
            existing_units=[{} for _ in range(27)],
            replacement_units=[{}, {}],
        )


def test_reparse_guard_allows_ordinary_updates_and_new_videos():
    VideoIngestService._ensure_reparse_does_not_regress(
        existing_units=[{} for _ in range(6)],
        replacement_units=[{}],
    )
    VideoIngestService._ensure_reparse_does_not_regress(
        existing_units=[{} for _ in range(10)],
        replacement_units=[{} for _ in range(5)],
    )

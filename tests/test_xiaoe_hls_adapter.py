from __future__ import annotations

from engines.content.xiaoe_hls_adapter import XiaoeHlsAdapter


def test_xiaoe_hls_adapter_extracts_video_id_from_page_url():
    adapter = XiaoeHlsAdapter()

    metadata = adapter.build_metadata(
        m3u8_url="https://example.com/path/index.m3u8?token=secret",
        page_url="https://appaoswidcd4711.h5.xiaoeknow.com/p/course/video/v_6a61f1c3e4b0694c352ea964",
        title="测试课程",
    )

    assert metadata["platform"] == "xiaoe"
    assert metadata["platform_video_id"] == "v_6a61f1c3e4b0694c352ea964"
    assert metadata["title"] == "测试课程"

def test_vendored_xiaoe_hls_poc_package_is_present():
    import xiaoe_hls_poc

    assert "已授权" in xiaoe_hls_poc.__doc__

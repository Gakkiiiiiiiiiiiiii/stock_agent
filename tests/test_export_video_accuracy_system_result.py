"""scripts/export_video_accuracy_system_result.py 冒烟测试（§11）。

用 SQLite 临时库 seed 真实 repository 数据，跑脚本 main，断言输出 JSON 结构
与 evaluation/video_accuracy/benchmark.py 的 system export 消费契约一致。
"""

from __future__ import annotations

import json

import pytest

import storage.models.content  # noqa: F401
import storage.models.knowledge  # noqa: F401
import storage.models.vector  # noqa: F401
from evaluation.video_accuracy.benchmark import load_system_export
from scripts.export_video_accuracy_system_result import export_system_result, main
from storage.db import SessionLocal
from storage.models.content import VideoAsset
from storage.repositories.knowledge_repository import KnowledgeRepository


def _seed_video() -> int:
    with SessionLocal() as session:
        video = VideoAsset(
            platform="bilibili",
            platform_video_id="BVEXPORT1",
            bvid="BVEXPORT1",
            url="https://example.com/BVEXPORT1",
            title="导出测试视频",
        )
        session.add(video)
        session.commit()
        return video.id


def _seed_knowledge(video_id: int) -> None:
    KnowledgeRepository().replace_video_knowledge(
        video_id=video_id,
        chapters=[
            {
                "chapter_index": 0,
                "start_ms": 0,
                "end_ms": 1000,
                "title": "章节",
                "chapter_type": "ANALYSIS",
                "primary_domain": "MARKET",
                "content_hash": "chapter-hash-export",
                "parser_version": "test",
            }
        ],
        units=[
            {
                "chapter_index": 0,
                "knowledge_uid": "ku-export-1",
                "primary_domain": "COMPANY",
                "knowledge_kind": "FINANCIAL_METRIC",
                "temporal_class": "SNAPSHOT",
                "expression_type": "AUTHOR_EXPLICIT",
                "subject_type": "EQUITY",
                "subject_key": "300750",
                "subject_name": "宁德时代",
                "predicate_key": "profit_growth",
                "statement": "宁德时代净利润增长20%",
                "canonical_statement": "宁德时代净利润增长20%",
                "sentiment": "BULLISH",
                "lifecycle_status": "ACTIVE",
                "support_status": "SOURCE_SUPPORTED",
                "support_score": 0.9,
                "speaker_id": "speaker_1",
                "content_hash": "unit-hash-export-1",
                "extractor_version": "test",
                "evidence": [
                    {
                        "source_type": "ASR",
                        "evidence_text": "宁德时代净利润增长20%",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "confidence_score": 0.9,
                        "is_primary": True,
                    }
                ],
                "entities": [
                    {"entity_type": "EQUITY", "entity_key": "300750", "entity_name": "宁德时代", "ticker": "300750"}
                ],
            }
        ],
    )


def test_export_script_output_matches_benchmark_contract(isolated_database, tmp_path):
    video_id = _seed_video()
    _seed_knowledge(video_id)
    output = tmp_path / "real_system_export.json"

    exit_code = main(["--video-ids", str(video_id), "--output", str(output)])

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert list(payload) == ["videos"]
    assert len(payload["videos"]) == 1
    video = payload["videos"][0]
    assert video["video_id"] == str(video_id)
    assert len(video["units"]) == 1
    unit = video["units"][0]
    # benchmark 消费的关键字段必须存在且正确。
    assert unit["statement"] == "宁德时代净利润增长20%"
    assert unit["canonical_statement"] == "宁德时代净利润增长20%"
    assert unit["support_status"] == "SOURCE_SUPPORTED"
    assert unit["support_score"] == 0.9
    assert unit["speaker_id"] == "speaker_1"
    assert unit["entities"][0]["entity_name"] == "宁德时代"
    assert unit["entities"][0]["ticker"] == "300750"
    # §16/§17：实体 confidence 无测量值时导出为 None 而非伪造默认值。
    assert unit["entities"][0]["confidence_score"] is None
    # benchmark.load_system_export 必须能直接消费该导出。
    loaded = load_system_export(output)
    assert loaded == {str(video_id): video["units"]}


def test_export_unknown_video_yields_empty_units(isolated_database):
    payload = export_system_result(["999999"])

    assert payload == {"videos": [{"video_id": "999999", "units": []}]}


def test_export_rejects_non_integer_video_id(isolated_database):
    with pytest.raises(ValueError, match="video_id"):
        export_system_result(["BVEXPORT1"])


def test_main_requires_video_ids(isolated_database, tmp_path):
    with pytest.raises(SystemExit):
        main(["--output", str(tmp_path / "out.json")])

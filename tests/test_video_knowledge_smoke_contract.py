from __future__ import annotations

from scripts.smoke_video_knowledge_pipeline import run_smoke


def test_simulated_video_knowledge_smoke_contract(tmp_path):
    result = run_smoke(tmp_path)

    assert result["ok"] is True
    assert result["chapter_count"] >= 1
    assert result["knowledge_unit_count"] >= 1
    assert result["evidence_coverage"] == 1.0
    assert result["vector_task_count"] >= result["knowledge_unit_count"]
    assert result["fake_qdrant_upsert_count"] >= 1
    assert result["fake_qdrant_delete_count"] >= 1
    assert result["search_hit_count"] >= 1
    assert result["lifecycle_audit_count"] >= 1

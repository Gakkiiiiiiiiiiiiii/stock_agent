from engines.factor.library import merge_library, save_library

def _factor(fid: str, rpn: list[str], candidate_hash: str, status: str = "OOS_PASS", fitness: float = 1.0):
    return {
        "id": fid,
        "rpn": rpn,
        "candidate_hash": candidate_hash,
        "status": status,
        "validation_stage": status,
        "metrics": {"fitness": fitness},
    }


def test_merge_library_preserves_latest_when_incoming_is_stale_snapshot():
    latest = {"factors": [_factor("F001", ["ret", "cs_rank"], "h1"), _factor("F002", ["volume", "cs_rank"], "h2")]}
    incoming = {"factors": [_factor("F001", ["ret", "cs_rank"], "h1")]}
    result = merge_library(latest, incoming)
    assert [item["id"] for item in result.library["factors"]] == ["F001", "F002"]


def test_merge_library_reassigns_id_conflict_and_keeps_status_from_downgrade():
    latest = {"factors": [_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE", fitness=2.0)]}
    incoming = {
        "factors": [
            _factor("F001", ["close", "cs_rank"], "h2"),
            _factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS", fitness=0.5),
        ]
    }
    result = merge_library(latest, incoming)
    assert len(result.library["factors"]) == 2
    assert result.library["factors"][0]["status"] == "ACTIVE"
    inserted = result.persisted_by_hash["h2"]
    assert inserted["id"] == "F002"
    assert result.reassigned_ids["F001"] == "F002"


def test_save_library_returns_persisted_ids_after_merge(tmp_path):
    path = tmp_path / "factor_library.yaml"
    save_library({"factors": [_factor("F001", ["ret", "cs_rank"], "h1")]}, path)
    stale = {"factors": [_factor("F001", ["close", "cs_rank"], "h2")]}
    result = save_library(stale, path)
    assert result.persisted_by_hash["h2"]["id"] == "F002"


def test_same_identity_preserves_canonical_id():
    latest = {"factors": [_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE")]}
    incoming = {"factors": [_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS")]}
    result = merge_library(latest, incoming)
    persisted = result.persisted_by_hash["h1"]
    assert persisted["id"] == "F001"
    assert persisted["status"] == "ACTIVE"


def test_same_identity_update_cannot_create_duplicate_ids():
    latest = {
        "factors": [
            _factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
            _factor("F009", ["volume", "cs_rank"], "h9"),
        ]
    }
    incoming = {"factors": [_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS")]}
    result = merge_library(latest, incoming)
    ids = [item["id"] for item in result.library["factors"]]
    assert ids == ["F001", "F009"]
    assert result.persisted_by_hash["h1"]["id"] == "F001"


def test_same_identity_preserves_immutable_fields():
    latest = {
        "factors": [
            {
                **_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
                "discovered_at": "2026-01-01T00:00:00+00:00",
                "research_run_id": "run-original",
                "metrics": {"fitness": 2.0, "final_oos_audit_ref": "factor-oos://202601/a.jsonl#h1"},
            }
        ]
    }
    incoming = {
        "factors": [
            {
                **_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
                "discovered_at": "2025-01-01T00:00:00+00:00",
                "research_run_id": "run-stale",
                "metrics": {"fitness": 0.5},
            }
        ]
    }
    result = merge_library(latest, incoming)
    persisted = result.persisted_by_hash["h1"]
    assert persisted["id"] == "F001"
    assert persisted["discovered_at"] == "2026-01-01T00:00:00+00:00"
    assert persisted["research_run_id"] == "run-original"
    assert persisted["metrics"]["fitness"] == 0.5  # 普通指标可被更新
    # incoming 缺失的 OOS 审计引用不得被擦除
    assert persisted["metrics"]["final_oos_audit_ref"] == "factor-oos://202601/a.jsonl#h1"


def test_rpn_match_backfills_missing_candidate_hash():
    legacy = {"id": "F001", "rpn": ["ret", "cs_rank"], "status": "OOS_PASS", "metrics": {}}
    latest = {"factors": [legacy]}
    incoming = {"factors": [_factor("F009", ["ret", "cs_rank"], "h1")]}
    result = merge_library(latest, incoming)
    assert len(result.library["factors"]) == 1
    persisted = result.library["factors"][0]
    assert persisted["id"] == "F001"
    assert persisted["candidate_hash"] == "h1"  # RPN 命中后补写缺失 Hash
    assert "h1" in result.updated_candidate_hashes


def test_existing_hash_not_overwritten_by_rpn_match():
    latest = {"factors": [_factor("F001", ["ret", "cs_rank"], "h-old")]}
    incoming = {"factors": [_factor("F009", ["ret", "cs_rank"], "h-new")]}
    result = merge_library(latest, incoming)
    # h-new 未命中 Hash 索引，但 RPN 命中同一因子：已有 Hash 不得修改
    persisted = result.library["factors"][0]
    assert persisted["candidate_hash"] == "h-old"
    assert len(result.library["factors"]) == 1


def test_library_uniqueness_check_blocks_duplicate_id(tmp_path):
    import pytest
    import yaml

    path = tmp_path / "factor_library.yaml"
    # 磁盘库被人为写入重复 id（不同 hash/rpn，Merge 无法自动修复）
    path.write_text(
        yaml.safe_dump(
            {
                "factors": [
                    _factor("F001", ["ret", "cs_rank"], "h1"),
                    _factor("F001", ["volume", "cs_rank"], "h2"),
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="DUPLICATE_FACTOR_ID"):
        save_library({"factors": []}, path)


def test_library_uniqueness_check_blocks_duplicate_rpn(tmp_path):
    import pytest
    import yaml

    path = tmp_path / "factor_library.yaml"
    # 磁盘库内已存在两条无 Hash 但 RPN 相同的记录（人为损坏）：
    # 唯一性终检必须拒绝落盘，而不是静默保存重复因子。
    path.write_text(
        yaml.safe_dump(
            {
                "factors": [
                    _factor("F001", ["ret", "cs_rank"], ""),
                    _factor("F002", ["ret", "cs_rank"], ""),
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="DUPLICATE_FACTOR_RPN"):
        save_library({"factors": []}, path)


def test_retired_factor_cannot_be_reactivated_by_worker():
    latest = {"factors": [_factor("F001", ["ret", "cs_rank"], "h1", status="RETIRED")]}
    incoming = {"factors": [_factor("F009", ["ret", "cs_rank"], "h1", status="ACTIVE")]}
    result = merge_library(latest, incoming)
    persisted = result.persisted_by_hash["h1"]
    assert persisted["id"] == "F001"
    assert persisted["status"] == "RETIRED"


def test_stale_incoming_audit_ref_does_not_overwrite_newer():
    latest = {
        "factors": [
            {
                **_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
                "metrics": {"final_oos_audit_ref": "factor-oos://202607/factor_oos_20260726.jsonl#h1"},
            }
        ]
    }
    incoming = {
        "factors": [
            {
                **_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
                "metrics": {"final_oos_audit_ref": "factor-oos://202601/factor_oos_20260101.jsonl#h1"},
            }
        ]
    }
    result = merge_library(latest, incoming)
    persisted = result.persisted_by_hash["h1"]
    assert persisted["metrics"]["final_oos_audit_ref"] == "factor-oos://202607/factor_oos_20260726.jsonl#h1"


# ---------- Metrics 按版本/时间合并（v2.2.3 第八轮 P1） ----------


def test_stale_metrics_cannot_overwrite_newer_metrics():
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
             "metrics": {"fitness": 2.0, "metrics_as_of": "2026-07-26"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
             "metrics": {"fitness": 0.5, "metrics_as_of": "2026-07-01"}}
        ]
    }
    result = merge_library(latest, incoming)
    assert result.persisted_by_hash["h1"]["metrics"]["fitness"] == 2.0


def test_newer_monitoring_metrics_can_update_existing():
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
             "metrics": {"fitness": 2.0, "metrics_as_of": "2026-07-01"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
             "metrics": {"fitness": 0.5, "metrics_as_of": "2026-07-26", "metrics_run_id": "run-new"}}
        ]
    }
    result = merge_library(latest, incoming)
    metrics = result.persisted_by_hash["h1"]["metrics"]
    assert metrics["fitness"] == 0.5
    assert metrics["metrics_as_of"] == "2026-07-26"
    assert metrics["metrics_run_id"] == "run-new"


def test_incoming_without_version_cannot_overwrite_versioned_metrics():
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
             "metrics": {"fitness": 2.0, "metrics_as_of": "2026-07-26"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
             "metrics": {"fitness": 0.5}}
        ]
    }
    result = merge_library(latest, incoming)
    assert result.persisted_by_hash["h1"]["metrics"]["fitness"] == 2.0


def test_both_unversioned_metrics_keep_legacy_merge_behavior():
    latest = {"factors": [{**_factor("F001", ["ret", "cs_rank"], "h1"), "metrics": {"fitness": 2.0}}]}
    incoming = {"factors": [{**_factor("F009", ["ret", "cs_rank"], "h1"), "metrics": {"fitness": 0.5}}]}
    result = merge_library(latest, incoming)
    assert result.persisted_by_hash["h1"]["metrics"]["fitness"] == 0.5


def test_final_oos_metrics_are_immutable():
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
             "metrics": {
                 "final_oos_summary": {"passed": True, "mean_rank_ic": 0.05},
                 "final_oos_audit_ref": "factor-oos://202607/factor_oos_20260726.jsonl#rid-1",
                 "discovery_rank_ic": 0.04,
                 "metrics_as_of": "2026-07-01",
             }}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
             "metrics": {
                 "final_oos_summary": {"passed": False},
                 "final_oos_audit_ref": "factor-oos://202601/factor_oos_20260101.jsonl#rid-0",
                 "discovery_rank_ic": 0.01,
                 "fitness": 0.5,
                 "metrics_as_of": "2026-07-26",
             }}
        ]
    }
    result = merge_library(latest, incoming)
    metrics = result.persisted_by_hash["h1"]["metrics"]
    # 研究类指标不可变，即使 incoming 更新
    assert metrics["final_oos_summary"] == {"passed": True, "mean_rank_ic": 0.05}
    assert metrics["final_oos_audit_ref"].endswith("#rid-1")
    assert metrics["discovery_rank_ic"] == 0.04
    # 普通监控指标允许被更新
    assert metrics["fitness"] == 0.5

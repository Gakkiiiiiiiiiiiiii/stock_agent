from engines.factor.library import merge_library, save_library, update_factor_monitoring

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
                "metrics": {"fitness": 0.5, "rank_ic_20d": 0.01},
            }
        ]
    }
    result = merge_library(latest, incoming)
    persisted = result.persisted_by_hash["h1"]
    assert persisted["id"] == "F001"
    assert persisted["discovered_at"] == "2026-01-01T00:00:00+00:00"
    assert persisted["research_run_id"] == "run-original"
    # Discovery 原始指标属于 research 语义，不可被覆盖
    assert persisted["metrics"]["fitness"] == 2.0
    # 监控类指标可被更新
    assert persisted["metrics"]["rank_ic_20d"] == 0.01
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
             "metrics": {"rank_ic_20d": 0.05, "metrics_as_of": "2026-07-26"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
             "metrics": {"rank_ic_20d": 0.01, "metrics_as_of": "2026-07-01"}}
        ]
    }
    result = merge_library(latest, incoming)
    assert result.persisted_by_hash["h1"]["metrics"]["rank_ic_20d"] == 0.05


def test_newer_monitoring_metrics_can_update_existing():
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
             "metrics": {"rank_ic_20d": 0.05, "metrics_as_of": "2026-07-01"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
             "metrics": {"rank_ic_20d": 0.01, "metrics_as_of": "2026-07-26", "metrics_run_id": "run-new"}}
        ]
    }
    result = merge_library(latest, incoming)
    metrics = result.persisted_by_hash["h1"]["metrics"]
    assert metrics["rank_ic_20d"] == 0.01
    assert metrics["metrics_as_of"] == "2026-07-26"
    assert metrics["metrics_run_id"] == "run-new"


def test_incoming_without_version_cannot_overwrite_versioned_metrics():
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
             "metrics": {"rank_ic_20d": 0.05, "metrics_as_of": "2026-07-26"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
             "metrics": {"rank_ic_20d": 0.01}}
        ]
    }
    result = merge_library(latest, incoming)
    assert result.persisted_by_hash["h1"]["metrics"]["rank_ic_20d"] == 0.05


def test_both_unversioned_metrics_keep_legacy_merge_behavior():
    latest = {"factors": [{**_factor("F001", ["ret", "cs_rank"], "h1"), "metrics": {"rank_ic_20d": 0.05}}]}
    incoming = {"factors": [{**_factor("F009", ["ret", "cs_rank"], "h1"), "metrics": {"rank_ic_20d": 0.01}}]}
    result = merge_library(latest, incoming)
    assert result.persisted_by_hash["h1"]["metrics"]["rank_ic_20d"] == 0.01


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
                 "rank_ic_20d": 0.02,
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
    assert metrics["rank_ic_20d"] == 0.02


# ---------- Research/Monitoring 拆分与 Freshness 修正（v2.2.4 第九轮） ----------


def test_research_metrics_are_nested_and_immutable():
    research = {
        "discovery": {"rank_ic": 0.04, "fitness": 1.0, "research_run_id": "run-1"},
        "final_oos": {"passed": True, "audit_ref": "factor-oos://202607/a.jsonl#rid"},
    }
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1", status="ACTIVE"),
             "metrics": {"research": research, "monitoring": {}, "metrics_as_of": "2026-07-01"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1", status="OOS_PASS"),
             "metrics": {
                 "research": {"discovery": {"rank_ic": 0.001}, "final_oos": {"passed": False}},
                 "metrics_as_of": "2026-07-26",
             }}
        ]
    }
    result = merge_library(latest, incoming)
    metrics = result.persisted_by_hash["h1"]["metrics"]
    assert metrics["research"]["discovery"]["rank_ic"] == 0.04
    assert metrics["research"]["final_oos"]["passed"] is True


def test_monitoring_metrics_can_update():
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1"),
             "metrics": {
                 "research": {"discovery": {"rank_ic": 0.04}},
                 "monitoring": {"as_of": "2026-07-01", "rank_ic_20d": 0.03},
             }}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1"),
             "metrics": {"monitoring": {"as_of": "2026-07-26", "rank_ic_20d": 0.01, "revision": 1}}}
        ]
    }
    result = merge_library(latest, incoming)
    metrics = result.persisted_by_hash["h1"]["metrics"]
    assert metrics["monitoring"]["rank_ic_20d"] == 0.01
    assert metrics["monitoring"]["as_of"] == "2026-07-26"
    assert metrics["research"]["discovery"]["rank_ic"] == 0.04


def test_stale_monitoring_metrics_cannot_overwrite():
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1"),
             "metrics": {"monitoring": {"as_of": "2026-07-26", "rank_ic_20d": 0.03}}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1"),
             "metrics": {"monitoring": {"as_of": "2026-07-01", "rank_ic_20d": 0.01}}}
        ]
    }
    result = merge_library(latest, incoming)
    assert result.persisted_by_hash["h1"]["metrics"]["monitoring"]["rank_ic_20d"] == 0.03


def test_original_discovery_rank_ic_cannot_be_overwritten():
    latest = {"factors": [{**_factor("F001", ["ret", "cs_rank"], "h1"), "metrics": {"rank_ic": 0.04, "icir": 0.6}}]}
    incoming = {"factors": [{**_factor("F009", ["ret", "cs_rank"], "h1"), "metrics": {"rank_ic": 0.001, "icir": 0.1}}]}
    result = merge_library(latest, incoming)
    metrics = result.persisted_by_hash["h1"]["metrics"]
    assert metrics["rank_ic"] == 0.04
    assert metrics["icir"] == 0.6


def test_original_discovery_fitness_cannot_be_overwritten():
    latest = {"factors": [{**_factor("F001", ["ret", "cs_rank"], "h1"), "metrics": {"fitness": 2.0, "coverage": 0.9}}]}
    incoming = {"factors": [{**_factor("F009", ["ret", "cs_rank"], "h1"), "metrics": {"fitness": 0.5, "coverage": 0.3}}]}
    result = merge_library(latest, incoming)
    metrics = result.persisted_by_hash["h1"]["metrics"]
    assert metrics["fitness"] == 2.0
    assert metrics["coverage"] == 0.9


def test_data_version_hash_does_not_define_freshness():
    # Hash 字典序不得被当作时间顺序：incoming 的 hash 更大但更旧，仍不得覆盖
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1"),
             "metrics": {"rank_ic_20d": 0.05, "metrics_as_of": "2026-07-26", "metrics_data_version": "aaaa"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1"),
             "metrics": {"rank_ic_20d": 0.01, "metrics_as_of": "2026-07-01", "metrics_data_version": "zzzz"}}
        ]
    }
    result = merge_library(latest, incoming)
    assert result.persisted_by_hash["h1"]["metrics"]["rank_ic_20d"] == 0.05


def test_same_timestamp_different_data_version_conflicts():
    import pytest

    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1"),
             "metrics": {"rank_ic_20d": 0.05, "metrics_as_of": "2026-07-26", "metrics_data_version": "aaaa"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1"),
             "metrics": {"rank_ic_20d": 0.01, "metrics_as_of": "2026-07-26", "metrics_data_version": "bbbb"}}
        ]
    }
    with pytest.raises(ValueError, match="METRICS_VERSION_CONFLICT"):
        merge_library(latest, incoming)


def test_higher_revision_wins():
    latest = {
        "factors": [
            {**_factor("F001", ["ret", "cs_rank"], "h1"),
             "metrics": {"rank_ic_20d": 0.05, "metrics_as_of": "2026-07-26", "metrics_revision": 1,
                         "metrics_data_version": "aaaa"}}
        ]
    }
    incoming = {
        "factors": [
            {**_factor("F009", ["ret", "cs_rank"], "h1"),
             "metrics": {"rank_ic_20d": 0.01, "metrics_as_of": "2026-07-26", "metrics_revision": 2,
                         "metrics_data_version": "bbbb"}}
        ]
    }
    result = merge_library(latest, incoming)
    metrics = result.persisted_by_hash["h1"]["metrics"]
    assert metrics["rank_ic_20d"] == 0.01
    assert metrics["metrics_revision"] == 2


# ---------- Monitoring 合并最终修复（v2.2.5 第十轮） ----------


def test_monitoring_update_works_when_existing_has_top_level_freshness():
    latest = {
        "factors": [
            {
                **_factor("F001", ["ret", "cs_rank"], "h1"),
                "metrics": {
                    "metrics_as_of": "2026-07-26",
                    "metrics_updated_at": "2026-07-26T12:00:00Z",
                    "research": {"discovery": {"rank_ic": 0.04}},
                    "monitoring": {"as_of": "2026-07-26", "revision": 1, "rank_ic_20d": 0.03},
                },
            }
        ]
    }
    incoming = {
        "factors": [
            {
                **_factor("F009", ["ret", "cs_rank"], "h1"),
                "metrics": {"monitoring": {"as_of": "2026-08-01", "revision": 2, "rank_ic_20d": 0.01}},
            }
        ]
    }
    result = merge_library(latest, incoming)
    metrics = result.persisted_by_hash["h1"]["metrics"]
    assert metrics["monitoring"]["rank_ic_20d"] == 0.01
    assert metrics["research"]["discovery"]["rank_ic"] == 0.04
    assert metrics["metrics_as_of"] == "2026-07-26"


def test_stale_monitoring_is_rejected_even_when_flat_metrics_are_newer():
    latest = {
        "factors": [
            {
                **_factor("F001", ["ret", "cs_rank"], "h1"),
                "metrics": {
                    "metrics_as_of": "2026-07-01",
                    "monitoring": {"as_of": "2026-08-01", "revision": 2, "rank_ic_20d": 0.03},
                },
            }
        ]
    }
    incoming = {
        "factors": [
            {
                **_factor("F009", ["ret", "cs_rank"], "h1"),
                "metrics": {
                    "metrics_as_of": "2026-08-02",
                    "monitoring": {"as_of": "2026-07-01", "revision": 1, "rank_ic_20d": 0.01},
                },
            }
        ]
    }
    result = merge_library(latest, incoming)
    assert result.persisted_by_hash["h1"]["metrics"]["monitoring"]["rank_ic_20d"] == 0.03


def test_monitoring_writer_increments_revision_and_preserves_research(tmp_path):
    path = tmp_path / "factor_library.yaml"
    save_library(
        {
            "factors": [
                {
                    **_factor("F001", ["ret", "cs_rank"], "h1"),
                    "metrics": {
                        "research": {"discovery": {"rank_ic": 0.04}},
                        "monitoring": {"as_of": "2026-07-26", "revision": 1, "rank_ic_20d": 0.03},
                    },
                }
            ]
        },
        path,
    )
    updated = update_factor_monitoring(
        "F001",
        {"rank_ic_20d": 0.02},
        as_of="2026-08-01",
        data_version="dv-1",
        updated_at="2026-08-01T12:00:00Z",
        path=path,
    )
    assert updated["metrics"]["monitoring"]["revision"] == 2
    assert updated["metrics"]["monitoring"]["rank_ic_20d"] == 0.02
    assert updated["metrics"]["research"]["discovery"]["rank_ic"] == 0.04


def test_monitoring_writer_rejects_unknown_data_version(tmp_path):
    import pytest

    path = tmp_path / "factor_library.yaml"
    save_library({"factors": [_factor("F001", ["ret", "cs_rank"], "h1")]}, path)
    with pytest.raises(ValueError, match="MONITORING_DATA_VERSION_REQUIRED"):
        update_factor_monitoring("F001", {"rank_ic_20d": 0.02}, as_of="2026-08-01", data_version="UNKNOWN", path=path)

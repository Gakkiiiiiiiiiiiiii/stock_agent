"""OOS 审计精确引用测试（v2.2.3 第八轮 P0）。"""
import json

import pytest

from engines.factor.oos_audit import (
    AuditWriteResult,
    append_oos_audit,
    build_audit_record_id,
    migrate_legacy_oos_audit_uri,
    read_oos_audit,
    resolve_oos_audit_uri,
)


@pytest.fixture(autouse=True)
def _audit_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTOR_OOS_AUDIT_ROOT", str(tmp_path / "audit"))
    return tmp_path / "audit"


def _read_all(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_append_returns_precise_uri(_audit_tmp):
    result = append_oos_audit({"research_run_id": "run-1", "candidate_hash": "abc123", "event": "FINAL_OOS_EVALUATED"})
    assert isinstance(result, AuditWriteResult)
    assert result.uri.startswith("factor-oos://")
    assert result.uri.endswith(f"#{result.record_id}")
    assert str(_audit_tmp) not in result.uri  # URI 不含本机绝对路径
    assert (_audit_tmp / result.relative_path).exists()


def test_record_id_contains_run_candidate_event_and_uuid():
    record_id = build_audit_record_id("run-1", "h1", "FINAL_OOS_EVALUATED")
    parts = record_id.split(":")
    assert parts[0] == "run-1"
    assert parts[1] == "h1"
    assert parts[2] == "FINAL_OOS_EVALUATED"
    assert len(parts[3]) == 32  # uuid4 hex


def test_final_oos_ref_remains_final_oos_after_id_assignment(_audit_tmp):
    evaluated = append_oos_audit({
        "event": "FINAL_OOS_EVALUATED",
        "research_run_id": "run-1",
        "candidate_hash": "h1",
    })
    append_oos_audit({
        "event": "FACTOR_ID_ASSIGNED",
        "research_run_id": "run-1",
        "candidate_hash": "h1",
        "factor_id": "F001",
        "parent_audit_record_id": evaluated.record_id,
    })
    record = read_oos_audit(evaluated.uri)
    assert record["event"] == "FINAL_OOS_EVALUATED"


def test_same_candidate_multiple_runs_have_distinct_uris(_audit_tmp):
    first = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-A", "candidate_hash": "hx"})
    second = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-B", "candidate_hash": "hx"})
    assert first.uri != second.uri
    assert first.record_id != second.record_id
    assert read_oos_audit(first.uri)["research_run_id"] == "run-A"
    assert read_oos_audit(second.uri)["research_run_id"] == "run-B"


def test_same_candidate_multiple_events_have_distinct_record_ids(_audit_tmp):
    evaluated = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-1", "candidate_hash": "h1"})
    assigned = append_oos_audit({"event": "FACTOR_ID_ASSIGNED", "research_run_id": "run-1", "candidate_hash": "h1", "factor_id": "F001"})
    assert evaluated.record_id != assigned.record_id
    assert evaluated.uri != assigned.uri


def test_factor_id_event_links_to_final_oos_record(_audit_tmp):
    evaluated = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-1", "candidate_hash": "h1"})
    assigned = append_oos_audit({
        "event": "FACTOR_ID_ASSIGNED",
        "research_run_id": "run-1",
        "candidate_hash": "h1",
        "factor_id": "F001",
        "parent_audit_record_id": evaluated.record_id,
        "parent_audit_uri": evaluated.uri,
    })
    record = read_oos_audit(assigned.uri)
    assert record["event"] == "FACTOR_ID_ASSIGNED"
    assert record["parent_audit_record_id"] == evaluated.record_id
    assert record["parent_audit_uri"] == evaluated.uri


def test_read_oos_audit_matches_record_id_not_candidate_hash(_audit_tmp):
    first = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-A", "candidate_hash": "hx", "final_oos": {"run": "A"}})
    append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-B", "candidate_hash": "hx", "final_oos": {"run": "B"}})
    record = read_oos_audit(first.uri)
    assert record["research_run_id"] == "run-A"
    assert record["final_oos"] == {"run": "A"}


def test_legacy_candidate_hash_fragment_rejected(_audit_tmp):
    result = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-1", "candidate_hash": "h1"})
    legacy_uri = result.uri.replace(f"#{result.record_id}", "#h1")
    with pytest.raises(ValueError, match="LEGACY_OOS_AUDIT_URI_AMBIGUOUS"):
        read_oos_audit(legacy_uri)


def test_legacy_fragment_allowed_only_with_opt_in(_audit_tmp):
    append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-1", "candidate_hash": "h1"})
    result = append_oos_audit({"event": "FACTOR_ID_ASSIGNED", "research_run_id": "run-1", "candidate_hash": "h1", "factor_id": "F001"})
    legacy_uri = result.uri.replace(f"#{result.record_id}", "#h1")
    record = read_oos_audit(legacy_uri, allow_legacy_candidate_fragment=True)
    assert record["candidate_hash"] == "h1"


def test_migrate_legacy_uri_requires_unique_match(_audit_tmp):
    result = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-1", "candidate_hash": "h1"})
    append_oos_audit({"event": "FACTOR_ID_ASSIGNED", "research_run_id": "run-1", "candidate_hash": "h1", "factor_id": "F001"})
    legacy_uri = result.uri.replace(f"#{result.record_id}", "#h1")
    # 不过滤时命中多条 → 拒绝
    with pytest.raises(ValueError, match="LEGACY_OOS_AUDIT_URI_AMBIGUOUS"):
        migrate_legacy_oos_audit_uri(legacy_uri)
    # 结合 event 过滤到唯一记录 → 迁移成功
    migrated = migrate_legacy_oos_audit_uri(legacy_uri, event="FINAL_OOS_EVALUATED")
    assert migrated.endswith(f"#{result.record_id}")
    assert read_oos_audit(migrated)["event"] == "FINAL_OOS_EVALUATED"


def test_migrate_new_format_uri_is_noop(_audit_tmp):
    result = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-1", "candidate_hash": "h1"})
    assert migrate_legacy_oos_audit_uri(result.uri) == result.uri


def test_read_oos_audit_not_found(_audit_tmp):
    result = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-1", "candidate_hash": "h1"})
    missing = result.uri.replace(result.record_id, "run-1:h1:FINAL_OOS_EVALUATED:" + "0" * 32)
    with pytest.raises(FileNotFoundError):
        read_oos_audit(missing)


def test_read_oos_audit_requires_record_id(_audit_tmp):
    result = append_oos_audit({"event": "FINAL_OOS_EVALUATED", "research_run_id": "run-1", "candidate_hash": "h1"})
    with pytest.raises(ValueError, match="OOS_AUDIT_URI_RECORD_ID_REQUIRED"):
        read_oos_audit(result.uri.split("#")[0])


def test_resolve_invalid_uri():
    with pytest.raises(ValueError, match="INVALID_OOS_AUDIT_URI"):
        resolve_oos_audit_uri("/opt/stock_agent/storage/audit/x.jsonl")


def test_audit_record_contains_written_at(_audit_tmp):
    result = append_oos_audit({"research_run_id": "run-2", "candidate_hash": "def456", "event": "FINAL_OOS_EVALUATED"})
    record = _read_all(_audit_tmp / result.relative_path)[0]
    assert record["audit_record_id"] == result.record_id
    assert "audit_written_at" in record

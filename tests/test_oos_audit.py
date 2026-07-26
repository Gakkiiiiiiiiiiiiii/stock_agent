"""OOS 审计 URI 可迁移化测试（v2.2.2 第七轮 P2）。"""
import json

import pytest

from engines.factor.oos_audit import (
    AuditWriteResult,
    append_oos_audit,
    read_oos_audit,
    resolve_oos_audit_uri,
)


@pytest.fixture(autouse=True)
def _audit_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTOR_OOS_AUDIT_ROOT", str(tmp_path / "audit"))
    return tmp_path / "audit"


def test_append_returns_migratable_uri(_audit_tmp):
    result = append_oos_audit({"research_run_id": "run-1", "candidate_hash": "abc123"})
    assert isinstance(result, AuditWriteResult)
    assert result.uri.startswith("factor-oos://")
    assert result.uri.endswith("#abc123")
    assert result.relative_path in result.uri
    assert str(_audit_tmp) not in result.uri  # URI 不含本机绝对路径
    assert result.record_id == "run-1:abc123"
    assert ( _audit_tmp / result.relative_path ).exists()


def test_audit_record_contains_record_id(_audit_tmp):
    result = append_oos_audit({"research_run_id": "run-2", "candidate_hash": "def456"})
    path = _audit_tmp / result.relative_path
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["audit_record_id"] == "run-2:def456"
    assert record["research_run_id"] == "run-2"
    assert record["candidate_hash"] == "def456"
    assert "audit_written_at" in record


def test_resolve_oos_audit_uri(_audit_tmp):
    result = append_oos_audit({"research_run_id": "run-3", "candidate_hash": "h9"})
    path, fragment = resolve_oos_audit_uri(result.uri)
    assert path == _audit_tmp / result.relative_path
    assert fragment == "h9"


def test_resolve_invalid_uri():
    with pytest.raises(ValueError, match="INVALID_OOS_AUDIT_URI"):
        resolve_oos_audit_uri("/opt/stock_agent/storage/audit/x.jsonl")


def test_read_oos_audit_roundtrip(_audit_tmp):
    result = append_oos_audit(
        {"research_run_id": "run-4", "candidate_hash": "hx", "event": "FINAL_OOS_EVALUATED", "factor_id": None}
    )
    record = read_oos_audit(result.uri)
    assert record["candidate_hash"] == "hx"
    assert record["event"] == "FINAL_OOS_EVALUATED"


def test_factor_id_assignment_event_traceable(_audit_tmp):
    evaluated = append_oos_audit(
        {"research_run_id": "run-5", "candidate_hash": "hz", "event": "FINAL_OOS_EVALUATED", "factor_id": None}
    )
    append_oos_audit(
        {"research_run_id": "run-5", "candidate_hash": "hz", "event": "FACTOR_ID_ASSIGNED", "factor_id": "F012"}
    )
    # 同一 URI 读取到最后一条匹配记录：ID 分配事件
    record = read_oos_audit(evaluated.uri)
    assert record["event"] == "FACTOR_ID_ASSIGNED"
    assert record["factor_id"] == "F012"


def test_read_oos_audit_not_found(_audit_tmp):
    result = append_oos_audit({"research_run_id": "run-6", "candidate_hash": "h1"})
    with pytest.raises(FileNotFoundError):
        read_oos_audit(result.uri.replace("#h1", "#missing"))

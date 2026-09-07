from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from financial_agent.utils import project_root
from storage import bootstrap


def test_sqlite_agent_migrations_apply_once(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}", future=True)
    monkeypatch.setattr(bootstrap, "get_engine", lambda: engine)
    bootstrap.apply_sql_migrations()
    bootstrap.apply_sql_migrations()
    with engine.connect() as conn:
        versions = conn.execute(text("SELECT version FROM schema_migration ORDER BY version")).scalars().all()
    assert versions == sorted(set(versions))
    assert "010_agent_learning_loop.sql" in versions
    assert "032_decision_input_bundle.sql" in versions
    assert "034_decision_input_bundle_patch.sql" in versions
    assert "038_decision_binding_constraints.sql" in versions
    assert "042_knowledge_conclusion_runs.sql" in versions
    assert "043_knowledge_conclusion_lineage_audit.sql" in versions
    assert "045_knowledge_conclusion_citation_quote_provenance.sql" in versions
    with engine.connect() as conn:
        citation_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(knowledge_conclusion_citation)"))}
    assert "quote_hash_provenance" in citation_columns


def test_postgres_agent_migration_selection_uses_backend_variants():
    paths = bootstrap._migration_paths_for_backend(project_root() / "storage" / "migrations", "postgres")
    assert any(path.name == "042_knowledge_conclusion_runs.sql" for path in paths)
    assert any(path.name == "043_knowledge_conclusion_lineage_audit.sql" for path in paths)


def test_043_lineage_audit_rows_are_append_only(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'lineage-audit.db'}", future=True)
    monkeypatch.setattr(bootstrap, "get_engine", lambda: engine)
    bootstrap.apply_sql_migrations()
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO knowledge_conclusion_run(
            conclusion_id,idempotency_key,request_hash,request_json,policy_version,state,version,created_at,updated_at)
            VALUES ('conclusion-1','key-1','request-hash','{}','policy','SUCCEEDED',1,'2026-09-06T00:00:00Z','2026-09-06T00:00:00Z')"""))
        conn.execute(text("""INSERT INTO knowledge_conclusion_lineage_audit(
            audit_id,conclusion_id,audit_hash,payload_json,created_at)
            VALUES ('audit-1','conclusion-1','audit-hash','{}','2026-09-06T00:00:00Z')"""))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE knowledge_conclusion_lineage_audit SET payload_json='tampered' WHERE audit_id='audit-1'"))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("DELETE FROM knowledge_conclusion_lineage_audit WHERE audit_id='audit-1'"))


def test_postgres_autoincrement_migration_is_rejected_when_no_variant(tmp_path):
    path = Path(tmp_path / "010_bad.sql")
    sql = "CREATE TABLE demo (id INTEGER PRIMARY KEY AUTOINCREMENT)"
    try:
        bootstrap._ensure_migration_sql_compatible(path, sql, "postgres")
    except RuntimeError as exc:
        assert "AUTOINCREMENT" in str(exc)
    else:
        raise AssertionError("expected SQLite AUTOINCREMENT migration to be rejected for postgres")


def test_decision_unit_and_replay_outcome_migrations_are_idempotent(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'uow.db'}", future=True)
    monkeypatch.setattr(bootstrap, "get_engine", lambda: engine)
    bootstrap.apply_sql_migrations()
    bootstrap.apply_sql_migrations()
    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert {"decision_requests", "decision_runs", "decision_outbox", "outbox", "decision_replay_runs", "decision_outcome_runs"} <= tables
        run_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(decision_runs)"))}
        assert {"state", "bundle_hash", "formal_result_hash", "governance_hash", "final_response_json", "final_response_hash", "lineage_json", "execution_authorization_json", "version", "readiness_snapshot_json"} <= run_columns
        unique_indexes = list(conn.execute(text("PRAGMA index_list(decision_requests)")))
        assert any(int(row[2]) == 1 for row in unique_indexes)
        replay_indexes = list(conn.execute(text("PRAGMA index_list(decision_replay_runs)")))
        assert any(int(row[2]) == 1 for row in replay_indexes)


def _prepare_038_history(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}", future=True)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.exec_driver_sql("CREATE TABLE investment_decision (id VARCHAR(64) PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE decision_input_bundle (bundle_id VARCHAR(64) PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE decision_snapshot_v3 (snapshot_id VARCHAR(64) PRIMARY KEY, decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id), schema_version VARCHAR(40) NOT NULL, bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id), snapshot_hash VARCHAR(64) NOT NULL UNIQUE, payload_json JSON NOT NULL, created_at TIMESTAMP NOT NULL)")
        conn.exec_driver_sql("CREATE TABLE specialist_artifact_v2 (artifact_id VARCHAR(64) PRIMARY KEY, decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id), bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id), artifact_hash VARCHAR(64) NOT NULL UNIQUE, payload_json JSON NOT NULL, created_at TIMESTAMP NOT NULL)")
        conn.exec_driver_sql("CREATE TABLE investment_proposal_v2 (proposal_id VARCHAR(64) PRIMARY KEY, decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id), bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id), proposal_hash VARCHAR(64) NOT NULL UNIQUE, payload_json JSON NOT NULL, created_at TIMESTAMP NOT NULL)")
        conn.exec_driver_sql("CREATE TABLE policy_evaluation_v2 (policy_result_id VARCHAR(64) PRIMARY KEY, decision_id VARCHAR(64) NOT NULL REFERENCES investment_decision(id), bundle_id VARCHAR(64) NOT NULL REFERENCES decision_input_bundle(bundle_id), result_hash VARCHAR(64) NOT NULL UNIQUE, payload_json JSON NOT NULL, created_at TIMESTAMP NOT NULL)")
        conn.exec_driver_sql("CREATE TABLE decision_memory_v2 (memory_id VARCHAR(64) PRIMARY KEY, memory_type VARCHAR(40) NOT NULL, memory_hash VARCHAR(64) NOT NULL UNIQUE, dedupe_key VARCHAR(128) NOT NULL, status VARCHAR(24) NOT NULL, payload_json JSON NOT NULL, created_at TIMESTAMP NOT NULL)")
    return engine


def _run_038(engine):
    path = project_root() / "storage" / "migrations" / "038_decision_binding_constraints.sql"
    sql = path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in (part.strip() for part in sql.split(";") if part.strip()):
            conn.exec_driver_sql(statement)


def _insert_anchor_history(engine, rows):
    with engine.begin() as conn:
        for decision_id, bundle_id in sorted({(decision, bundle) for decision, bundle in rows}):
            conn.execute(text("INSERT INTO investment_decision(id) VALUES (:id) ON CONFLICT DO NOTHING"), {"id": decision_id})
            conn.execute(text("INSERT INTO decision_input_bundle(bundle_id) VALUES (:id) ON CONFLICT DO NOTHING"), {"id": bundle_id})
        for index, (decision_id, bundle_id) in enumerate(rows):
            conn.execute(text("INSERT INTO specialist_artifact_v2(artifact_id,decision_id,bundle_id,artifact_hash,payload_json,created_at) VALUES (:id,:decision,:bundle,:hash,'{}','2026-08-24T00:00:00Z')"), {"id": f"a-{index}", "decision": decision_id, "bundle": bundle_id, "hash": f"ah-{index}"})
            conn.execute(text("INSERT INTO investment_proposal_v2(proposal_id,decision_id,bundle_id,proposal_hash,payload_json,created_at) VALUES (:id,:decision,:bundle,:hash,'{}','2026-08-24T00:00:01Z')"), {"id": f"p-{index}", "decision": decision_id, "bundle": bundle_id, "hash": f"ph-{index}"})
            conn.execute(text("INSERT INTO policy_evaluation_v2(policy_result_id,decision_id,bundle_id,result_hash,payload_json,created_at) VALUES (:id,:decision,:bundle,:hash,'{}','2026-08-24T00:00:02Z')"), {"id": f"q-{index}", "decision": decision_id, "bundle": bundle_id, "hash": f"qh-{index}"})


def test_038_backfills_consistent_historical_formal_lineage_and_constraints(tmp_path):
    engine = _prepare_038_history(tmp_path)
    _insert_anchor_history(engine, [("d1", "b1")])
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO decision_snapshot_v3(snapshot_id,decision_id,schema_version,bundle_id,snapshot_hash,payload_json,created_at) VALUES ('s1','d1','decision.snapshot.v3','b1','sh1','{}','2026-08-24T00:00:03Z')"))
    _run_038(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT decision_id,bundle_id FROM decision_bundle_binding")).one() == ("d1", "b1")
        indexes = {row[1] for row in conn.execute(text("PRAGMA index_list('decision_snapshot_v3')"))}
        memory_indexes = {row[1] for row in conn.execute(text("PRAGMA index_list('decision_memory_v2')"))}
        binding_indexes = {row[1] for row in conn.execute(text("PRAGMA index_list('decision_bundle_binding')"))}
    assert "ux_decision_snapshot_v3_bundle_id" in indexes
    assert "ux_decision_memory_v2_dedupe_key" in memory_indexes
    assert any("sqlite_autoindex_decision_bundle_binding" in name for name in binding_indexes)


@pytest.mark.parametrize("rows", [
    [("d1", "b1"), ("d1", "b2")],
    [("d1", "b1"), ("d2", "b1")],
])
def test_038_historical_cross_lineage_fails_closed(tmp_path, rows):
    engine = _prepare_038_history(tmp_path)
    _insert_anchor_history(engine, rows)
    with pytest.raises(IntegrityError):
        _run_038(engine)
    with engine.connect() as conn:
        # SQLite DDL is transactional only in part: the anchor table may
        # remain after the failed upgrade, but no conflicting historical row
        # may be committed and no source rows may be deleted.
        assert conn.execute(text("SELECT COUNT(*) FROM decision_bundle_binding")).scalar_one() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM specialist_artifact_v2")).scalar_one() == len(rows)

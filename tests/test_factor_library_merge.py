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

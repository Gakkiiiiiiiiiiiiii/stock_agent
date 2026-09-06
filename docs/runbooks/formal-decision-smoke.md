# Formal decision smoke and evidence boundary

## Purpose

This runbook verifies repository behavior without calling a live upstream,
starting Compose, changing a deployed database, or creating a formal decision.
It is the appropriate check after documentation or local orchestration changes.
It is not a release, deployment, or cross-repository acceptance procedure.

## Safe local verification

From the repository root, run:

```powershell
python scripts/contracts/verify_manifest.py
python -m pytest -q tests/test_formal_readiness.py tests/test_decision_replay_v2.py tests/test_replay_outcome_exactly_once.py tests/test_decision_authority_v2.py --basetemp=.pytest_sa_p2_01
python -m pytest -q tests/architecture --basetemp=.pytest_sa_p2_01_architecture
```

The test fixture uses SQLite and offline/deterministic fakes. It verifies that
formal readiness fails closed, analysis remains explicitly non-authoritative,
`EXACT_REPLAY` is snapshot anchored, and replay/outcome leases can recover or
fence a stale owner. It does not contact quant, stock_content, stock_factor,
or a broker.

## Readiness interpretation

- `GET /health/formal-decision-ready`: a `200` means the configured readiness
  probes accepted their current inputs; a `503` returns blocking `reason_codes`.
  A `503` means do not call the formal creation endpoint as a recovery action.
- `GET /health/analysis-ready`: may be available while formal readiness is
  blocked. It must be treated as `ANALYSIS_ONLY`, never as a formal fallback.
- `POST /api/v2/decisions`: requires a non-empty `idempotency_key` and a
  timezone-aware `as_of`. It is permitted only after formal readiness succeeds
  in the intended environment; it persists a decision and is therefore outside
  this safe smoke procedure.

## Formal smoke fixture

The repository contains a deployment smoke script at
[`tests/smoke/test_deployed_decision_api.sh`](../../tests/smoke/test_deployed_decision_api.sh).
Its CI values set `STOCK_AGENT_DETERMINISTIC_FIXTURE=1` and SQLite, and expect
a non-executable `HOLD`, snapshot retrieval, and `EXACT_REPLAY`. Use that only
in the configured CI smoke environment. It is deliberately not proof of a real
producer response, production `.env`, PostgreSQL migration/recovery, or a
cross-repository deployment.

## Replay and outcome recovery

Use `POST /api/v2/decisions/{decision_id}/replay` with
`{"mode":"EXACT_REPLAY"}` only for an existing persisted v3 snapshot. Preserve
the original response and discrepancy data when it fails; follow the
[replay mismatch runbook](replay-mismatch.md).

`POST /api/v2/decisions/{decision_id}/outcomes/refresh` reads an outcome through
the configured provider and persists the result, so it is not part of the safe
smoke procedure. It needs a timezone-aware `measured_at` and a `horizon`.
If its lease is busy, its provider is unavailable, or a retry conflicts with
the durable result, recover the owner/provider and preserve the original
result; do not edit outcome or lease rows manually.

## Explicitly blocked evidence

The following are not established by the commands above and must remain
BLOCKED until their designated integration/deployment owner supplies successful
evidence:

- real PostgreSQL migration, concurrency, lease recovery, and result identity;
- real quant, stock_factor, and stock_content readiness and contract handshake;
- production or Compose `.env` correctness and an end-to-end deployed stack;
- any real usage, execution, broker, or trading-safety assertion.

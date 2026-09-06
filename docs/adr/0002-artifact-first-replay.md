# ADR-0002: Artifact-first replay

Formal calculations consume a frozen evidence Bundle and immutable snapshots.
EXACT replay uses the persisted snapshot, policy and specialist artifacts and
never calls an LLM or current upstream data. Mismatches create a discrepancy;
historical results are never overwritten.

The replay/outcome run stores use leases, fencing, and result-identity checks
to make a recovered retry return the durable result or reject a conflicting
payload. This behavior is covered with isolated SQLite tests; it is not a
claim of verified PostgreSQL recovery behavior. Operational handling is in the
[replay mismatch runbook](../runbooks/replay-mismatch.md) and the
[formal smoke runbook](../runbooks/formal-decision-smoke.md).

# Replay mismatch

Treat `decision_replay_mismatch_total` or an `EXACT_REPLAY` mismatch as an
incident. Preserve the original snapshot and finalized response. Inspect the
returned discrepancy/diff data, then compare the producer commit, policy
version, contract reference, bundle/evidence hashes, and snapshot identity.
Never overwrite a finalized decision to make a replay pass.

For `POST /api/v2/decisions/{decision_id}/replay`, use `EXACT_REPLAY` to check
the persisted v3 snapshot. It does not fall back to current upstream data or a
legacy adapter. A missing persisted snapshot is a `404`; invalid replay input
is rejected rather than reconstructed from current state.

If a replay worker was interrupted, retry only through the same replay entry
point. The persistent lease can recover an expired owner and fences a stale
owner; do not update replay-run rows by hand. The local evidence for this
recovery behavior is SQLite-only, so a real PostgreSQL incident remains open
until it is reproduced or verified in the approved integration environment.

Outcome refreshes follow the same preservation rule: they use a durable lease
and freeze the returned market-result identity. A retry returns the stored
identical result; a conflicting payload is rejected. `OUTCOME_LEASE_BUSY` or
dependency-unavailable responses require recovery of the worker/provider, not
manual replacement of the stored result.

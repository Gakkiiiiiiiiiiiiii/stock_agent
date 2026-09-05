# ADR-0002: Artifact-first replay

Formal calculations consume a frozen evidence Bundle and immutable snapshots.
EXACT replay uses the persisted snapshot, policy and specialist artifacts and
never calls an LLM or current upstream data. Mismatches create a discrepancy;
historical results are never overwritten.

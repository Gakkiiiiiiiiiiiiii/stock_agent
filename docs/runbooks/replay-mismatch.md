# Replay mismatch

Treat `decision_replay_mismatch_total` as an incident. Preserve the original
snapshot/result, inspect the discrepancy artifact and compare producer commit,
policy version and artifact hashes. Never overwrite a finalized decision.

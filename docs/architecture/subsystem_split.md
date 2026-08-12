# Subsystem split migration

This repository is in the strangler phase described by
`stock_agent_subsystem_split_design_20260811.md`.

- `contracts/content.py` freezes `content.v1` and `content-factor-signal.v1`.
- `contracts/factor.py` freezes `factor.v1`.
- `clients/` is the only allowed HTTP boundary from `stock_agent` to the two
  extracted services.
- `CONTENT_BACKEND` and `FACTOR_BACKEND` default to `local`. Set either to
  `remote` with its corresponding service URL to cut traffic over; reverting to
  `local` is the rollback path.

The legacy engines deliberately remain during shadow validation. They must not
be removed until the external services pass their golden/regression gates.

## Current delivery boundary

This change completes Phase 0 and establishes the independently runnable Phase
1/5 service boundaries. It intentionally does **not** claim a completed data
copy, media-adapter move, factor-engine move, or production cutover. Those are
the next ordered migrations: move content adapters/repositories and run Content
Shadow first; then move factor core behind its three provider ports and run
Factor Shadow. Keeping the local defaults until those gates pass is the
documented rollback-safe behaviour.

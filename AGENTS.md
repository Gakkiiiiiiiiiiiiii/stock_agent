# AGENTS.md

## Repository Ownership

This repository owns investment-analysis and formal decision workflows based on
frozen evidence bundles. It does not own `quant`, `stock_content`, or
`stock_factor` implementations. Cross-repository use must go through explicit
HTTP clients, contracts, or documented adapter boundaries.

## Safety Boundaries

- Default to deterministic fixtures, analysis-only, replay, shadow, or paper
  validation. Do not submit orders, connect to broker/QMT execution paths, or
  enable LIVE behavior without explicit user authorization for that exact action.
- Formal decision requests require the repository's readiness, contract,
  freshness, PIT, snapshot, lineage, audit, and idempotency gates.
- Preserve fail-closed behavior; do not fabricate evidence when upstream
  services or local dependencies are unavailable.

## Change Rules

- Make the smallest coherent patch and preserve v2 bundle-first semantics.
- Do not import other repositories directly or move their responsibilities here.
- Public contract, schema, or API changes need compatibility coverage.
- Do not weaken tests, safety controls, replay/snapshot guarantees, or
  execution-eligibility boundaries to make a patch pass.
- Avoid generated storage, model/media data, environment secrets, and Docker
  runtime output unless the task specifically requires them.

## Validation

Prefer the repository's local environment and existing scripts:

    .\.conda-env\python.exe -m pytest -q -m "not integration and not media and not qmt and not slow"
    .\.conda-env\python.exe -m pytest tests/architecture -q
    .\.conda-env\python.exe scripts\check_skill_contracts.py

Record unavailable Docker, embedding, database, or external-service dependencies
as environment gates. Do not bypass them with business-code changes.

## Conversation Workflow Trigger

Treat a user message beginning with the following exact form as a workflow
control instruction:

    @开工 <task-id> [economy|safe] :: <feature request>

`economy` is the default. Confirm the parsed task id, mode, and request in one
line, then take action without asking for a plan confirmation unless the form
is incomplete or the requested action is unsafe.

- In `economy`, delegate all source edits to exactly one `terra_implementer`.
  At most one narrowly scoped, read-only Luna explorer or tester may run beside
  it. Do not let the parent or any second agent edit source files.
- In `safe`, first obtain a read-only `sol_reviewer` assessment, then delegate
  source edits to exactly one `terra_implementer`; request a final Sol review
  for the changed invariants. `sol_implementer` is allowed only when an
  explicit escalation documents why Terra is insufficient.
- Check the worktree status before edits and preserve unrelated user changes.
  If this chat is not in an isolated worktree, state that fact and offer the
  repository launcher rather than silently mixing a feature into the base
  checkout.
- Do not edit another repository. Report the required contract change as a
  bounded task for that repository or for `@联调`.

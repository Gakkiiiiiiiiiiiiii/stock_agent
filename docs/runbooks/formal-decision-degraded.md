# Formal decision degraded

Check `GET /health/formal-decision-ready` and inspect its stable
`reason_codes`. A non-ready result is HTTP `503`; it blocks `POST
/api/v2/decisions`. Do not retry it as a formal decision, relax a threshold, or
use an analysis response as a substitute.

`GET /health/analysis-ready` may still report `ready: true` with
`degraded: true`, `authority: ANALYSIS_ONLY`, and the same reason codes. This
is only a read-only analysis availability signal; every such response remains
`execution_eligible=false`.

Recover the named dependency or manifest/configuration mismatch first, then
run the non-mutating checks in the [formal smoke runbook](formal-decision-smoke.md).
Do not mark the incident resolved merely because a deterministic fixture passes:
that fixture is not a real producer, contract handshake, or PostgreSQL proof.

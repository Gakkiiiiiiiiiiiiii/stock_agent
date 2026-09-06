# ADR-0001: Investment decision authority

`stock_agent` is the sole authority for formal investment decisions. Only
`POST /api/v2/decisions` can produce a formal result. Analysis and compatibility
responses are explicitly non-authoritative and always have
`execution_eligible=false`.

This ADR defines an ownership boundary; it is not evidence that a deployment is
formally ready. Current repository-level verification uses isolated SQLite and
deterministic/fake dependencies. Real PostgreSQL, live producer availability,
and the cross-repository contract handshake require separate integration
evidence. See the [formal smoke runbook](../runbooks/formal-decision-smoke.md).

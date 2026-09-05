# Formal decision degraded

Check `/health/formal-decision-ready` and inspect its stable `reason_codes`.
When any upstream contract, freshness, PIT, quality or lineage gate fails,
formal creation returns a blocking response. Continue serving analysis only;
do not widen thresholds or enable LIVE execution. Restore the named upstream,
then rerun deterministic readiness and replay checks.

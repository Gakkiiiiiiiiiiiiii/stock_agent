# Formal decision SLOs

Track formal availability, P95 stage latency, upstream freshness compliance,
replay match rate, outbox lag and outcome-evaluation lag. Use low-cardinality
labels (`stage`, `result`, `reason_code`, `component`); keep decision and
request identities in trace/log context only.

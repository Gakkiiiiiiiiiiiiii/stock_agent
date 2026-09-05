# Decision authority v2/v3 boundary notes

This repository owns orchestration, deterministic policy, evidence bundles,
snapshots, replay contracts and the model/HTTP reliability boundaries. The
`stock_content`, `stock_factor` and `quant` implementations remain outside
this repository and are consumed only through `clients/` and versioned
contracts.

## Reliability boundary

`app/model_gateway/ModelGateway` is the single model execution boundary. Its
routes are provider-neutral callables and provide bounded retries, 429
`Retry-After`, circuit breaking, token/cost accounting, structured output
validation, metrics and `TraceContext`. Provider-specific
`/chat/completions` handling lives in the gateway transport adapter; business
orchestration must not issue that HTTP request directly.

`clients/_http.py` is the shared remote-subsystem boundary. It uses explicit
connect/read/write/pool timeouts, injected retry sleeper/clock/request
function, a circuit breaker and propagated decision/bundle/agent/proposal/
snapshot identifiers. Failures are normalized to
`DEPENDENCY_TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `CONTRACT_MISMATCH`,
`STALE_DATA` or `INVALID_SNAPSHOT`. Existing read-only quant/factor/content
client method names remain compatible.

Read clients accept only the controlled trace metadata fields and attach
contract-version headers. Response envelope metadata is preserved when
`data` is unwrapped. EvidenceGateway dispatches only operation allowlists and
maps typed dependency error codes directly into `DependencyStatus`.

## Time boundary

`engines.market.trading_clock.TradingClock` is the main-path business clock.
It is injectable and has an offline weekday calendar by default; a remote
calendar adapter can be supplied at composition time. The retired
`ExchangeTradingCalendar`/QMT producer path is no longer packaged or imported.
Timestamps used
for infrastructure/audit records may continue to use explicit UTC providers.
Snapshot and quant calendar adapters expose open/closed state, navigation,
holiday handling, half-day close times, and pre-open/open/after-close phases.
The weekday implementation is explicitly degraded and must not be treated as
an exchange holiday calendar.

Metrics are recorded into a bounded, thread-safe aggregate sink and rendered
as one Prometheus series per metric name and label set.

## Contract and migration compatibility

DecisionInputBundle, SpecialistArtifact v2, Proposal v2, Policy v2 and
DecisionSnapshot v3 are immutable, hashed contracts. New persistence tables
are additive migrations (032--038). Migration 038 adds one-to-one uniqueness
for snapshot decision/bundle anchors, a database-level decision/bundle binding
anchor, and a database-unique decision-memory dedupe key. Upgrades must audit
and resolve historical duplicate or cross-linked rows before the constraint is
applied; the migration fails closed and never deletes data automatically.
Consumers should validate the declared schema version and retain the v1 adapters until all replay/evaluation clients
have migrated. Snapshot v3 is the authoritative lineage boundary; normal
model tool execution is not a substitute for a frozen bundle.

## Current integration point

The formal runtime consumes `ClaudeAgent.run_formal` when a model is enabled.
The runtime still owns bundle construction, specialist execution, policy and
snapshot persistence. The D3 runtime wiring must pass its specialist artifact
list into the formal context so the model receives exactly the same frozen
artifacts that are recorded in the snapshot; this note does not claim that
integration is complete.

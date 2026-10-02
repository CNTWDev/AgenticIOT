# ADR 0005 — Independent virtual Edge and durable pull execution

Date: 2026-09-14  
Status: Accepted for local simulation

## Context

Registry and typed capabilities work. The next useful slice must distinguish durable
acceptance from delivery and evidence-backed completion, without prematurely adding
brokers, physical protocols, an Agent runtime or a human/domain directory.

## Decision

- Add a runtime module and additive migration `0003_runtime` with Edge, Binding, Command, Receipt, Observation and StateProjection tables.
- Use an independently launched Python virtual Edge with outbound loopback HTTP polling and an explicit Edge credential, separate from operator/viewer credentials.
- Scope binding and commands by the server-resolved domain and Edge identity. Allow one immutable binding per device and only the known low-risk virtual-light actions.
- Use the committed Command row as the durable pull queue. Initial receipt, policy evidence and audit commit atomically with acceptance. A separate broker/outbox is unnecessary until the dispatch transport changes.
- Serialize conflicting requests with PostgreSQL locks and a caller-scoped unique idempotency index. Results and receipts are append-only through the API; identical redelivery is safe.
- Confirm only correlated, post-dispatch observations matching the requested value. Preserve timeout evidence when a late report confirms historical execution.
- Keep observations append-first and derive a latest-state projection with freshness, without changing device metadata revisions on every report.
- Persist simulated state changes, processed-command evidence and upload outbox atomically in SQLite. Lock one local journal per runtime process and retain it across restarts.
- The Admin requests actions and renders real server responses; all virtual execution is visibly labeled simulated.

## Consequences and limits

The golden path can now be tested through independent HTTP client/server processes,
without an AI platform or physical device. No Agent placement decision is required.
Changing to a real transport must preserve command semantics but cannot inherit the
simulator's atomic device-state guarantee.

This is not a production Edge channel: static local tokens, one writer, no mTLS,
fencing, offline grant cache, high-risk approval or physical protocol retries. Current
policy is fixed to authenticated domain operators and allowlisted low-risk actions.
Fresh synchronous reads, event streaming and post-confirmation divergence are deferred.
Deadline transitions are settled on API reads/polls/reports, not by a background timer.

The initial Command queue has no throughput/retention SLA. Virtual Edge processes at
most one command per five-second cycle and supports up to 200 assigned virtual lights.
Before physical deployment, add a protocol-specific failure harness and secure device
enrollment, and define retention, observability and credential lifecycle requirements.

## Validation

Database tests cover durable acceptance, concurrent idempotency, domain/Edge isolation,
observation sequence/replay/freshness, evidence correlation, expiry, late confirmation
and journal restart. Adapter tests reject expired, unsupported and non-simulated jobs.
Desktop/mobile browser tests launch a real standalone Edge process, bind a light,
submit a command and verify its resulting observed state and ordered receipts.

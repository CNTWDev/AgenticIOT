# ADR 0012 Pilot recovery and transport bounds

Status: Accepted for software pilot. Date: 2026-10-02.

This amends ADR 0011 and architecture v0.3 following reproduced reconnect, clock-skew and command-fencing failures. It preserves metadata-only inference, the device/service boundary and single-process deployment. Real hardware and public-network qualification remain separate gates.

## Accepted boundaries

Receipt, execution and observation are different facts. A durable Node acknowledgement records receipt, not execution. Missing acknowledgement cannot prove nonexecution. Unknown outcomes never trigger automatic physical retries or cloud fallback.

The Node journal retains execution intents, results and durable rejection tombstones. It must stay with the same Node identity. Journal loss is an operator incident, not normal reconnect recovery.

## Testable rules

1. Protocol v2 hello advertises 1–16 outstanding command slots and Node time; welcome includes server time. Dispatch respects outstanding credits; an overloaded Node replies `busy`. Eight execution slots and four observation slots remain independent; same-device actions remain serialized.
2. Expected transport/handshake failures retry with exponential backoff and jitter, capped at approximately 38 seconds including jitter. Configuration and journal-identity failures are not silently swallowed. Platform drain returns HTTP 503 with Retry-After where ASGI denial responses are supported.
3. The Node persists receipt before scheduling execution. Reconciliation replays saved results. Without a saved result or active task, it first writes a tombstone, then reports `not_executed` or `uncertain`. An execution intent prevents a nonexecution claim. A tombstone blocks later delivery of the same command ID.
4. `not_executed` releases the fence with a failed receipt. An uncertain report establishes a barrier but keeps dispatch paused. A domain manager may release it only using a fresh, latest, post-barrier observation and a reason. Actor, reason and observation are recorded; the prior outcome stays unknown. This does not certify arbitrary physical machinery as stopped.
5. Result causality uses command identity and increasing journal sequence, not cross-machine wall-clock ordering. Raw time is preserved; the handshake supplies an approximate offset for freshness. This is not secure time attestation and does not replace host clock synchronization.
6. Permanent evidence conflicts are quarantined; temporary rejections reconnect and retain evidence. Outbox delivery uses a one-record acknowledgement window, five-second retry interval and immediate progress after acknowledgement. Logs contain sequence/error codes, not payloads.
7. Acceptance wakes dispatch in-process; the fallback scan is every two seconds. Heartbeat is independent, every five seconds. Indexed command fence flags replace receipt-history scans. Command-list and dispatch locks follow ascending creation time and ID. Resource updates use PostgreSQL NO KEY UPDATE where identifiers do not change, allowing concurrent foreign-key checks without a device/Node lock cycle.
8. The last active domain-manager grant cannot be revoked. A signed `turn_ref`, when present, namespaces device idempotency keys in addition to existing issuer/client/caller/domain boundaries. Deterministic runtimes must still inject stable operation IDs; non-idempotent actions remain unsupported.
9. Inference uses one admission-based 60-second deadline. Expiry allows five seconds for bookkeeping, not more generation time. Known-offline Nodes are rejected before consuming a key. Success sends no cancellation. Provider usage requests are opt-in per local-service configuration.
10. Admission separates 64 ordinary HTTP requests (including inference), 32 event streams and 64 Node connections. Liveness is independent. Mutation bodies have a five-second total read deadline. Trailing-slash API paths return 404, not a reconstructed redirect.

## Retention and deployment

Inference metadata/deduplication retain 30 days; events retain 1000 entries per domain. Maintenance removes at most 500 ordinary observations older than 30 days per pass, excluding current projections, command evidence and reconciliation evidence. This cleanup rate is not a hard storage ceiling. Commands, receipts and Node intents/results/tombstones/quarantined evidence are retained for recovery and deduplication. Operators must monitor disk growth and retain protected backups.

Upgrades refresh build bases and validate identity configuration before downtime. PostgreSQL starts before backup; `pending` starts immediately before migration. Earlier failure can restore the previously running release. After migration starts, failure stays fail-closed. Upgrade platform before Nodes, retaining each journal; v1 channels remain accepted during transition but lack v2 calibration and reconciliation.

## Exclusions and validation

No broker, Redis, multi-worker deployment, cloud fallback or prompt storage is added. Logs expose reconnect types, clock estimates, dispatch counts and quarantine codes; a metrics backend and sustained-capacity benchmark remain future work. The execution acceptance scope is still low-risk simulated adapters.

Regression includes actual localhost WebSocket transport with 24 commands, both directions of clock skew, missing-ack uncertainty, persistent tombstones, manager reconciliation and deployment failure ordering. It does not replace household NAT/Wi-Fi tests, real-model compatibility, crash/power-loss tests, load soaks or backup-restoration drills.

# Pilot recovery validation

Date: 2026-10-02. Scope: software regression for ADR 0012. This record supplements the initial software-pilot validation; it is not a public-network or physical-device acceptance report.

Local validation uses macOS, Python 3.13 and a dedicated PostgreSQL 14 test cluster. A newly created disposable database exercised the complete migration chain through `0011_runtime_recovery`, including the recovery/index migration. Production Compose targets PostgreSQL 18; GitHub CI separately validates that version and Linux containers.

## Regression coverage

- 79 non-integration tests cover deployment control flow, pre-migration backup failure, secret-safe preflight, retryable handshakes, durable tombstones, request-body timeout and separate admission buckets, alongside existing adapter/schema coverage.
- 30 PostgreSQL integration tests cover access, immutable model versions, evidence, 24-command WebSocket bursts, local simulated-model streaming, clock offsets of plus/minus 60 seconds, unknown-result reconciliation, cross-domain denial, latest-observation checks, turn-scoped keys and last-manager protection.
- 14 desktop/mobile browser tests cover registry, simulated execution, service activation and the one-time Node secret guard.
- Bash syntax, ShellCheck, Ruff, OpenAPI generation, ESLint, Prettier, TypeScript and the production Admin build are required checks.

The expanded burst test exposed a foreign-key lock cycle beyond the originally reported list/dispatch ordering issue. Resource-row locks now use NO KEY UPDATE where IDs are stable, allowing concurrent FK KEY SHARE checks while still serializing writers. Repeat burst runs are included before publication; a single successful run is not a sustained-load result.

## Remaining validation

Substitute-command deployment tests do not prove a real distro package installation or backup restoration. The local simulated model is not an Ollama compatibility result. Synthetic clock offsets do not prove robustness against arbitrary clock jumps or authenticated-but-malicious Nodes. Proxy restarts, household NAT/Wi-Fi, packet loss, prolonged disconnects, disk exhaustion and power-loss recovery still require dedicated fault-injection and target-environment trials.

No real device was actuated, cloud server purchased or production deployment changed during this review. Publishing source does not clear the existing production, hardware or licensing gates.

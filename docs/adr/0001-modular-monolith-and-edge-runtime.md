# ADR 0001: Modular Monolith and Separate Edge Runtime

Status: Accepted  
Date: 2026-09-13

## Context

The platform needs clear domain boundaries and independently deployable physical-device access, but the initial team and workload do not justify distributed cloud services.

## Decision

Implement the control and runtime plane as a modular monolith backed by PostgreSQL. Implement Edge Runtime as a separate deployable process. Use internal application-service boundaries, domain events, and a transactional outbox so modules can be extracted later without changing public contracts.

## Consequences

- lower initial operational and testing cost;
- transactional consistency for core registry and command acceptance;
- Edge deployment remains independent of cloud deployment;
- module ownership must be enforced in code and tests, not by network boundaries;
- later service extraction is possible but not promised or scheduled.

## Rejected alternatives

- Microservices from day one: excessive failure modes and contract overhead before the domain stabilizes.
- One process including device drivers: prevents remote Edge deployment and expands the control-plane attack surface.
- Cloud-only device execution: fails offline, latency, privacy, and legacy-protocol requirements.

# Implementation Plan

Status: Historical plan; superseded for future delivery  
Version: 0.1

The active sequence is [Implementation Plan v0.3](implementation-plan.md), following [ADR 0009](../adr/0009-node-local-services-and-agent-access.md). Completed slices below remain historical evidence; their remaining milestone order is not the current plan.

## Delivery strategy

Build one vertical slice before broad protocol coverage. The reference Thing is a virtual dimmable light with `power`, `brightness`, `set_power`, `set_brightness`, and `power_changed` capabilities.

## Milestone 0 — Repository foundation

Implementation: workspace foundation is present. See [development guide](../development.md)
for startup, validation and the distinction between planned and implemented APIs.

- backend and Admin Console workspace;
- formatting, linting, tests, and local configuration;
- PostgreSQL development environment;
- migration framework;
- OpenAPI validation and generated documentation;
- architecture-decision checks in pull requests.

Exit: empty services start locally and CI executes deterministic checks.

## Milestone 1 — Registry and capability model

Implementation: model publication, capability validation, device registration,
discovery, metadata editing and Admin pages are implemented. The corresponding
[API guide](../api/registry.md) and [ADR 0004](../adr/0004-registry-vertical-slice.md)
define the registry slice. Virtual light Adapter, independently authenticated Edge
registration and immutable bindings are now implemented for local simulation.

- DeviceModel and immutable versioning;
- CapabilitySpec validation;
- Device registration and lifecycle status;
- virtual Adapter and Edge Runtime registration;
- Management API and minimal Admin pages.

Exit: an operator registers a virtual light and inspects its typed capabilities.

## Milestone 2 — State and command golden path

Implementation: the virtual-light golden path passes against a standalone Edge process
on desktop and mobile. Durable command polling, caller-scoped idempotency, observations,
freshness, ordered receipts and timeout/late-evidence handling are implemented. See
[execution API](../api/execution.md) and [ADR 0005](../adr/0005-virtual-edge-execution.md).
This is a deliberately constrained slice: a fixed low-risk local policy, not external
grants or production hardware execution. Event streaming, synchronous fresh reads and
post-confirmation divergence remain deferred.

- observations and latest-state projection;
- Command aggregate and idempotency;
- policy enforcement hook;
- dispatch to virtual Edge;
- receipts, confirmation, timeout, and audit;
- Capability API and command timeline UI.

Exit: the README golden path passes as an automated end-to-end test.

## Milestone 3 — Real Edge Runtime

Implementation: a standalone Edge, durable local journal/outbox, minimal Adapter contract,
virtual adapter and real loopback MQTT transport to a simulated device are implemented.
The conformance/fault harness and desktop/mobile golden paths pass. This does not complete
physical onboarding, production authentication or writer fencing. See [ADR 0006](../adr/0006-minimal-adapters-and-mqtt-demo.md).

Next is deliberately limited to one physical target, using [the first-device intake and
acceptance plan](../hardware/first-device.md) and [ADR 0007](../adr/0007-first-physical-device-gates.md):

1. Confirm manufacturer/model/firmware and official protocol; do not assume a generic MQTT driver.
2. Implement and validate read-only identity, constrained access and truthful observations.
3. Separately approve one low-risk action and pass its physical fault acceptance matrix.

Device selection is currently pending. Do not add an HTTP adapter or multiple protocols just
to complete an earlier technology list. Secure channel/configuration synchronization and
fencing remain requirements to design against actual deployment needs, not completed features.

Exit: the chosen physical device passes read-only and separately approved action/fault
tests, with explicit uncertainty and recovery semantics. Simulator success alone no longer
satisfies this milestone; do not claim exactly-once physical effects without device evidence.

## Milestone 4 — External integration

- external OIDC integration;
- subject/domain/policy references;
- SSE and webhook delivery;
- MCP adapter generated from capability descriptions;
- high-risk action confirmation flow.

Exit: an external Agent discovers and invokes an authorized capability without receiving device protocol details or credentials.

## Milestone 5 — Hardware breadth

- BLE adapter;
- one ZigBee/Matter ecosystem bridge;
- adapter packaging and signed updates;
- device and Edge software lifecycle;
- failure-injection and recovery test suite.

Exit: distinct device transports use the same Capability API and command semantics.

## Cross-cutting quality gates

- no command bypasses policy enforcement and durable acceptance;
- every terminal command has receipts and audit correlation;
- API tests verify idempotency and stale-state behavior;
- adapter tests cover timeout, disconnect, duplicate, and late acknowledgement;
- secrets never appear in descriptions, events, logs, or Admin responses;
- offline behavior is declared per capability and tested;
- new infrastructure requires an ADR with a measured reason.

## Deliberately deferred

- microservice decomposition;
- arbitrary workflow builder;
- time-series analytics platform;
- autonomous multi-agent execution inside AgenticIoT;
- generalized digital-twin simulation;
- user/social graph;
- broad hardware support before the golden path is stable.

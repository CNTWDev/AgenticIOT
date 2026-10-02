# AgenticIoT

AgenticIoT is an AI-native device and Node-local service access platform. It gives authorized agents and applications typed, auditable interfaces to discover and operate devices and reach approved services behind private-network Nodes.

The platform owns resource authorization, node connectivity, device execution evidence, captured device events, and local-service invocation metadata. Entry products own Agent activation and any cloud-model policy. The platform does not own human relationships, memory, planning, a cloud-model gateway, GPU scheduling or payment settlement. This describes the target architecture; implemented capabilities are listed below.

## Architecture status

Technical Architecture v0.3 defines accepted boundaries and testable rules as of 2026-10-02. Performance and compatibility remain unvalidated:

- [Current technical architecture](docs/architecture/device-node-services-v0.3.md)
- [ADR 0009 scope and runtime revision](docs/adr/0009-node-local-services-and-agent-access.md)
- [Current implementation plan](docs/architecture/implementation-plan.md)
- [Accounts and resource activation](docs/adr/0010-account-resource-activation.md)
- [Async Node runtime](docs/adr/0011-async-node-runtime.md)
- [Node and local-service setup](docs/api/node-local-services.md)
- [Relay and scheduling experiment plan](docs/experiments/node-relay-validation.md)
- [Software validation and remaining release gates](docs/experiments/software-pilot-validation.md)
- [Historical v0.2 architecture](docs/architecture/device-ai-services-v0.2.md)

- [Platform charter](docs/architecture/platform-charter.md)
- [Historical v0.1 system architecture](docs/architecture/system-architecture.md)
- [Domain model](docs/architecture/domain-model.md)
- [Command lifecycle](docs/architecture/command-lifecycle.md)
- [Edge runtime](docs/architecture/edge-runtime.md)
- [Admin console](docs/architecture/admin-console.md)
- [API principles](docs/api/api-principles.md)
- [Initial OpenAPI contract](api/openapi.yaml)

Architecture decisions are recorded under [`docs/adr`](docs/adr).

The initial scope is a cloud platform with independent Nodes, Node-local request-scoped
text streaming, internal resource domains, thin Agent tools and captured-device event
subscriptions. Cloud model routing and fallback are deferred. Each older subdocument
marks its applicability; ADR 0008 remains unchanged as a historical decision, amended
by ADR 0009. Architecture acceptance does not mean implementation or experiment completion.

## Development status

The foundation and first registry slice are implemented: immutable device-model
versions, validated property/action/event schemas, domain-scoped device registration,
discovery, conditional metadata editing, transactional audit and working Admin pages.
Records persist in PostgreSQL. Local API credentials default to deny-all.

A standalone virtual Edge now registers with its own credential, reports observations,
executes durable low-risk light commands and uploads correlated confirmation receipts.
The Admin includes binding, state freshness and a command timeline. This is simulation,
not physical-device support. Signed entry validation is implemented; production identity
integration, TLS deployment and physical-device validation remain pending.
Registering or binding a device does not by itself make it connected.

The Edge now hosts a minimal standard Adapter interface with `virtual-light-v1` and
`mqtt-light-demo-v1`. The latter uses real loopback MQTT transport to a separate simulated
device, not household hardware. Adapters are advertised during registration and selected
in the Admin. External execution has a durable intent journal: an interrupted/unknown
result is not blindly resent. See the [MQTT Adapter guide](docs/api/mqtt-adapter.md).

The physical-device track remains [target confirmation → read-only verification → separately
approved actuation](docs/hardware/first-device.md). A brand/model has not yet been selected;
this plan does not enable hardware control or change the current simulated-only boundaries.

The v0.3 software path now includes internal domains and explicit grants, Ed25519 entry
tokens, independently provisioned Node credentials, WSS scheduling, metadata-only local
text streaming, typed HTTP Agent tools and replayable sampled-device events. The Admin
manages Node credentials and explicit service approval. The default app no longer mounts
the legacy Edge HTTP polling routes. This is a software pilot, not a production release:
real-model compatibility, household/cloud latency and hardware gates remain open.

Start with the [local development guide](docs/development.md).

Linux server installation, Git-based upgrades, backups and constrained rollback are
documented in the [Linux deployment guide](docs/deployment/linux.md). The installer is
`scripts/deploy.sh`; it requires Docker Engine with Compose v2 and defaults to loopback-only access.

```sh
docker compose up --build -d
```

Admin: http://127.0.0.1:5173 · Implemented API docs: http://127.0.0.1:8000/docs

Without Docker, see the native development instructions. The API can start without a
database; readiness correctly returns 503 until PostgreSQL and migrations are ready.
Configure your own local API credential and explicitly run
`python -m agenticiot.access.service` after migration before using device management.
No built-in administrator token is supplied; application startup never grants access.

The [implemented API snapshot](api/platform.openapi.json) is distinct from the
[planned Capability API reference](docs/api/capability-reference.md).
The [registry API guide](docs/api/registry.md) describes the implemented boundaries.
See the [Node setup guide](docs/api/node-local-services.md) to provision a Node and run
the golden path. Compose does not start or provision a Node automatically.

## Golden path

The first product milestone is deliberately small:

```text
publish a virtual light model and its power/brightness capabilities
  -> register a device pinned to that model version
  -> discover it through the public API
  -> invoke an action
  -> execute through an Edge Runtime
  -> confirm the observed device state
  -> expose the receipt and audit trail in the Admin Console
```

No additional protocol or device family should delay this end-to-end path.

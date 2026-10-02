# Platform Charter

Status: Accepted baseline  
Version: 0.3  
Scope: Product and architecture boundary

Confirmed: 2026-10-02. [Technical Architecture v0.3](device-node-services-v0.3.md) and [ADR 0009](../adr/0009-node-local-services-and-agent-access.md) define current boundaries and testable rules. Performance and compatibility are pending validation. ADR 0008 remains historical and unchanged; this charter describes targets, not implemented capabilities.

## Positioning

AgenticIoT is an **AI-native device and Node-local service access platform**: it connects, describes, discovers, observes, and operates physical devices, and connects authorized callers to approved services reachable through private-network Nodes.

The short form "OT management platform" remains usable for the physical-device side. The complete positioning includes device execution and AI service invocation, without implying an Agent Platform, GPU scheduler, or household identity system.

## Product promise

Authorized agents and applications use typed device and inference contracts without handling device protocols, vendor SDKs, node transport, or provider credentials. Deployment and data-path restrictions remain visible and enforceable rather than hidden behind location transparency.

The platform validates requests, records what was attempted, and distinguishes observed results from unknown outcomes. It does not make model output deterministic or guarantee exactly-once physical side effects.

## Responsibilities

AgenticIoT owns:

1. **Device onboarding and identity** — discovery, registration, credentials, trust posture, assignment, and retirement.
2. **Protocol adaptation** — replaceable, explicitly implemented and tested device adapters; the architecture does not imply support for every radio, protocol or vendor.
3. **Capability description** — protocol-independent properties, actions, events, constraints, and protocol bindings.
4. **State and events** — observations and captured events with freshness, sampled/native provenance, uncertainty and bounded replay; not a guarantee of complete physical-event capture.
5. **Command execution management** — validation, authorization enforcement, fixed-binding dispatch, idempotency, timeout, result confirmation, and reconciliation.
6. **Resource lifecycle** — registration, approved configuration, health, credential lifecycle, disablement and retirement. General firmware/update orchestration is deferred.
7. **Node operation** — approved local protocol and inference-service access, outbound connectivity, execution intent logging, and result reporting. Offline authorization and cloud-edge synchronization are not initial-release responsibilities.
8. **Resource-side security** — final enforcement of device and action safety regardless of the caller.
9. **Auditability** — immutable command history, actor and origin references, decisions, evidence, and diagnostics.
10. **Stable northbound interfaces** — discover, read, invoke, and subscribe for agents and applications.
11. **Node-local service access** — approved connections and models, compatibility profiles, request-scoped text streams and minimal invocation metadata. No persisted prompts, result replay, cloud-provider routing or fallback in the first release.
12. **Agent integration** — thin tools over device APIs and authorized event subscriptions; the entry or Agent Runtime handles deterministic event deduplication and Agent activation.

## Explicit non-responsibilities

Account clarification (ADR 0010): a verified human or service principal must have a resource-domain grant before managing activation. Nodes use separate machine credentials; local services are registered resources, not another user-account system. Registration, binding, enablement, connectivity and service health remain distinct. First-domain provisioning is explicit and never inferred from an untrusted entry.

AgenticIoT does not own:

- natural-language understanding, reasoning, task planning, or agent memory;
- human accounts, authentication UX, MFA, family relationships, organizations, or social graphs;
- personal profiles, long-term preferences, calendars, email, music, or general SaaS orchestration;
- vertical business workflows or a universal low-code automation product;
- model training, deployment or GPU provisioning; prompt management, multi-agent coordination, or an agent marketplace;
- payment settlement, platform-funded public inference by default, or a compute marketplace;
- first-release cloud-model gateway, provider failover or cloud-key management;
- the direct exposure of device drivers or device credentials to an LLM;
- a general BI product, data lake, or digital-twin simulation suite.

These capabilities may integrate with the platform but remain independently owned.

## Domain boundary

Human and organizational semantics belong to the entry product and its trusted backend. AgenticIoT owns only the resource-isolation and authorization records needed for enforcement:

- `subject_ref`
- internally generated `domain_id`
- DomainAlias mapping trusted issuer/client/external-domain identities to the internal domain
- DomainGrant recording the platform-authorized ceiling for that entry in that domain

The trusted entry backend asserts identity and delegated permissions. Effective authority is the intersection of verified token permissions, current platform Grants and resource safety constraints. Alias mapping alone grants no access. Cross-entry linking requires explicit authorized approval; revoking an entry Grant does not move or delete resources. Domain management is scoped self-service, not permission to increase one's own ceiling. Node credentials are never user/operator credentials.

Device trust domains, protocol fabrics, network membership, and physical topology remain inside AgenticIoT because they are required to reach and protect devices.

## Non-negotiable principles

1. **Capability over protocol** — public APIs describe what a thing can do, not how its radio works.
2. **Deterministic core, probabilistic edge caller** — an AI may propose; the platform validates and executes.
3. **Device protocol execution stays near the device** — initial authorization and dispatch still traverse the cloud; latency and offline suitability must be measured and disclosed, not inferred from local execution.
4. **Runtime roles are composable** — phones, speakers and gateways can host different roles; shared hardware does not merge permissions. Initial device bindings do not imply automatic failover between nodes.
5. **Offline is not implicit** — initial cloud delivery does not promise offline execution or strictly local data. A later complete local deployment requires its own verification and remains single-authority.
6. **Least privilege and zero implicit trust** — every command carries a principal, origin, authorization, scope, expiry, and trace.
7. **Observed state beats assumed state** — accepted or dispatched is not equivalent to physically completed.
8. **Secure by lifecycle** — onboarding, operation, credential rotation, disablement and retirement preserve authorization boundaries; broad firmware orchestration is not required to ship the first slice.
9. **Adapters are replaceable** — vendor or protocol code cannot leak into the canonical domain model.
10. **Start as a modular monolith** — preserve domain boundaries without paying an early distributed-systems tax.
11. **Open contracts, minimal infrastructure** — OpenAPI and adapter contracts first; new infrastructure only after measured need.
12. **Less is more** — a feature is core only when it directly improves private-device or approved Node-service access, safety, reliability, or operability.
13. **Unknown is not failed** — ambiguous execution does not authorize replay, provider fallback, or claims of zero cost.
14. **Declared local is not verified local** — a Node script cannot attest to all host egress. Enforce the path we control, expose locality declarations honestly, and reject strict-local requirements when no verified path exists.
15. **Stable intent is code-owned** — all commands require idempotency; non-idempotent or unknown actions require trusted stable operation identity, not LLM-generated keys.

## Architectural litmus test

A proposed feature belongs in AgenticIoT only if at least one answer is yes:

- Does it connect or identify a physical device?
- Does it describe or discover a device capability?
- Does it observe physical state or deliver a physical event?
- Does it make physical execution safer or more reliable?
- Does it operate or diagnose the device lifecycle?
- Is it necessary to safely reach or diagnose an approved private-network service through a Node, rather than a general model-gateway feature?

If all answers are no, the feature belongs above or beside the platform.

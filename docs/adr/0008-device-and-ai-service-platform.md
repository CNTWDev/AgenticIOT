# ADR 0008 Device and AI Service Platform

Status: Accepted  
Date: 2026-10-02  
Scope: v0.2 architecture and initial delivery boundary

## Context

The device-only baseline does not describe access to existing local model services or authorized cloud providers. Adding a separate household brain, GPU scheduler, or human-domain platform would exceed the agreed scope. The user approved the consolidated proposal on 2026-10-02.

## Decision

The authoritative baseline is [Technical Architecture v0.2](../architecture/device-ai-services-v0.2.md).

- The entry product owns human identity, household and organization semantics, current context, agent selection, consent UX and commercial arrangements. Its trusted backend or trusted issuer supplies verifiable authority; the IoT platform enforces resource isolation.
- Two business modules, Devices and AI Services, share Access and Nodes. These are explicit code boundaries within a modular monolith, not separate microservices.
- Nodes expose approved existing inference services and device adapters. Cloud model providers use service connections without fictional device or Node identities. No GPU provisioning or model deployment is included.
- Device Commands and AI Invocations retain separate state machines and evidence. Model output never directly authorizes physical actuation.
- Initial delivery is one cloud backend process, PostgreSQL, the existing Admin application and independently deployed Nodes. The first AI contract is text generation. WSS serves nodes, HTTPS serves APIs and cloud connectors, and bounded SSE serves inference output.
- Initial routing uses one preferred target and one optional approved fallback. Unknown execution is not automatically retried; partial outputs from different providers are never merged. Both data transfer and expense require authorization.
- Service accounts are explicitly provisioned and authorized; the platform does not automatically pay for users. Accounting settlement, media streaming, model marketplaces and generic workflow engines are excluded.
- Strictly local data paths and offline operation are not initial cloud-release promises. Full local deployment is a later single-authority option; cloud-edge dual writers are excluded.
- Business records provide durable work queues. A mandatory second outbox or broker is unnecessary until external message publication creates a measured need.

## Relationship to earlier decisions

ADR 0001's modular-monolith direction remains valid. ADR 0002's external human-domain ownership remains valid; it does not require a separate Identity & Domain product, and cached offline grants are deferred. This decision supersedes conflicting v0.1 assumptions about first-release offline execution, automatic device-owner takeover, mandatory outbox infrastructure and device-only product scope.

ADRs 0003–0006 remain records of implemented slices, not claims that the new modules exist. ADR 0007's physical-device gates remain mandatory. Existing simulated-only restrictions and implemented API contracts do not change with publication of this decision.

## Consequences

Extract shared node and authorization interfaces incrementally while preserving device identities, bindings and receipts. Do not reuse device Command as a universal AI job, duplicate Edge identities as compute nodes, or let the transport module decide business outcomes.

The single-process deployment has explicit availability and scaling limits. Horizontal scaling, local deployment and federated operation require separate verified designs before being advertised. Input persistence, encryption, retention, quotas, credential rotation and recovery tests are release gates.

Detailed schemas, protocol frames and operational values must be completed before their implementation or production use. Architecture acceptance is not implementation acceptance.

## Rejected alternatives

- A separate domain brain: duplicates entry-product responsibilities.
- One universal job/state machine: obscures the difference between probabilistic inference and physical side effects.
- Immediate microservices or a general scheduler: adds infrastructure without a demonstrated need.
- Implicit cloud fallback on any timeout: can duplicate processing and charges or violate data-transfer authority.

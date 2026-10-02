# ADR 0002: Human Domain Is External

Status: Accepted  
Date: 2026-09-13

## Context

Physical resources need authorization based on people, households, organizations, roles, delegation, and consent. Modeling these relationships inside the IoT/OT platform would turn it into an identity and personal-data platform.

## Decision

Human identity and relationship semantics are owned by an external Identity & Domain Platform. AgenticIoT accepts verifiable identity and authorization claims, stores opaque subject/domain/space/policy references, and performs the final resource-side authorization and safety enforcement.

Device identity, Edge identity, device protocol fabrics, connection leases, and device trust posture remain inside AgenticIoT.

## Consequences

- one person can participate in many domains and one domain can contain many people without changing the device model;
- AgenticIoT can integrate with different identity products;
- local/offline execution requires bounded, expiring cached grants;
- the platform still owns enforcement and cannot blindly trust caller-supplied identifiers;
- the Admin Console configures mappings but does not become an identity-management UI.

## Rejected alternatives

- Build a complete household/person graph inside AgenticIoT: violates product scope and creates privacy risk.
- Delegate all authorization to the AI Agent: probabilistic callers cannot be the final safety boundary.
- Store only device ACLs with local usernames: prevents cross-domain delegation and external identity integration.

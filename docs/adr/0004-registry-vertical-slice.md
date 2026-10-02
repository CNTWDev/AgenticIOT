# ADR 0004 — Registry vertical slice and local authorization

Date: 2026-09-14  
Status: Accepted for local development

## Context

The foundation is runnable, but useful Agent/device interaction requires durable,
typed and discoverable resources before adding execution or protocol integrations.
External identity integration is scheduled later; this must not expose anonymous
device data or silently move human/domain ownership into the IoT platform.

## Decision

- Keep registration and capability definitions in one `registry` module in the modular monolith.
- Publish immutable, domain-scoped model versions; pin every device to one version.
- Validate bounded inline JSON Schema 2020-12 definitions, without reference retrieval.
- Persist models, devices and successful change audits in PostgreSQL migration `0002_registry`.
- Enforce domain predicates in the service and composite domain/model foreign keys in the database.
- Use database uniqueness constraints and optimistic device revisions; persist successful changes and audit atomically.
- Provide Management API and a thin React console; expose discovery through the same authorized domain boundary.
- Require explicit local Bearer credentials mapped to external subject/domain references and operator/viewer roles. Default to deny-all.
- Keep credentials out of browser storage and response bodies. This local bootstrap is not production identity management.
- Mark every newly registered device `commissioning / unknown`. A capability declaration is not execution evidence.

## Consequences

An operator can publish, register, inspect and edit without an Agent platform or
physical device. The UI and API use real persistence and authorization rather than
mock online data. Cloud or on-device Agent placement does not change these contracts.

The slice intentionally excludes Edge enrollment, observation projection, commands,
receipts, OIDC/grant verification, human membership management and high-risk approval.
Virtual Adapter/Edge registration remains unfinished Milestone 1 work. The bootstrap
auth will be replaced while preserving resource-level enforcement. Production use
also needs transport security, token lifecycle, quotas and security-event auditing.

## Validation

Unit and database tests cover schema rejection, secret handling, default denial,
cross-domain access, viewer restrictions, immutable versions, uniqueness, concurrent
writes, conditional edits, paging and transactional audit. Browser tests exercise the
real PostgreSQL-backed API on desktop and mobile. See `docs/development.md` for commands
and the recorded local verification scope.

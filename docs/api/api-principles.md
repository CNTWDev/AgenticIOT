# API Principles

Status: Proposed for implementation  
Version: 0.1

Applicability (2026-10-02): [v0.3](../architecture/device-node-services-v0.3.md) and [ADR 0009](../adr/0009-node-local-services-and-agent-access.md) govern current contracts. Device actions remain asynchronous with mandatory idempotency; Node-local inference is a request-scoped POST stream with optional duplicate-dispatch protection and no result replay. Device events use retained cursors and disclose sampled/native provenance; inference streams do not share event replay guarantees. SSE is first, webhooks deferred. External domain references below must resolve to an internal domain_id and an active platform Grant. Examples are conceptual, not the implemented OpenAPI snapshot.

## API families

- **Capability API** for agents and applications: discover, read, invoke, subscribe.
- **Management API** for the Admin Console and operators: models, devices, Edge nodes, adapters, lifecycle, policy bindings, audit.
- **Edge Protocol** for Edge Runtime sessions and synchronization.
- **Adapter SDK** for southbound protocol plugins.

Management and runtime permissions are distinct even when served by one backend.

## General conventions

- Base path: `/v1`
- Media type: `application/json`
- API description: OpenAPI 3.1
- Resource identifiers are opaque strings.
- Timestamps use UTC RFC 3339 with explicit offset.
- List endpoints use stable cursor pagination.
- Errors use `application/problem+json`.
- Request correlation uses `traceparent` when supplied and returns a `trace_id`.
- Mutating management resources use optimistic concurrency with `ETag`/`If-Match`.
- Action invocation requires `Idempotency-Key` or an equivalent body field.

## Capability API

```text
GET  /v1/things
GET  /v1/things/{thing_id}
GET  /v1/things/{thing_id}/state
POST /v1/things/{thing_id}/actions/{action_name}
GET  /v1/commands/{command_id}
GET  /v1/commands/{command_id}/receipts
GET  /v1/events
POST /v1/webhooks
```

The Thing resource returns canonical capabilities, not raw adapter endpoints. A caller must not infer authorization from discovery: every read, invocation, and subscription is independently authorized.

## Asynchronous actions

Action invocation returns `202 Accepted` and a durable Command resource. The response includes a `Location` header pointing to `/v1/commands/{command_id}`.

Fast devices follow the same contract. The platform may include already available receipts in the response but never changes the meaning of HTTP completion into proof of physical completion.

## Request context

The authenticated token identifies the caller. Additional delegation and execution context may contain:

```json
{
  "subject_ref": "person:123",
  "domain_ref": "home:456",
  "space_ref": "room:living-room",
  "agent_ref": "agent:personal",
  "origin_ref": "runtime:phone-123",
  "authorization_ref": "grant:abc",
  "expires_at": "2026-09-13T15:05:00Z"
}
```

Context is untrusted input until verified. `subject_ref` in a JSON body cannot override the authenticated subject or delegated grant.

## State representation

Every state value includes:

- value and schema version;
- observation source;
- `observed_at` and `received_at`;
- freshness classification;
- source version or sequence when available;
- quality or error metadata.

An optional `fresh=true` request asks the runtime to perform a device read. It may fail when the device sleeps or is offline; the API may still return the last observation clearly marked stale.

## Event delivery

SSE is the initial interactive subscription mechanism. Webhooks support server-to-server delivery. Both use stable event IDs and resumable cursors. Delivery is at least once; consumers deduplicate by event ID.

Event retention is bounded. A cursor older than retention produces an explicit resynchronization error rather than silently skipping history.

## Error model

Problem responses include:

```json
{
  "type": "https://agenticiot.dev/problems/device-offline",
  "title": "Device is offline",
  "status": 409,
  "detail": "No eligible route can reach the target before the command deadline.",
  "code": "device_offline",
  "trace_id": "trace_01J..."
}
```

Stable machine-readable codes are part of the public contract. Human messages may evolve and be localized.

## Compatibility

- Backward-compatible fields may be added within `/v1`.
- Existing meaning, type, or constraints cannot change silently.
- DeviceModel and CapabilitySpec versions are explicit and immutable after publication.
- Protocol adapter versions are operational metadata, not public capability versions.
- MCP and other agent-facing adapters version independently from the Capability API.

## Security boundary

The API authenticates external identities using an integrated standards-based provider. AgenticIoT performs resource-side authorization and safety checks for every operation. Public API credentials never grant direct access to Edge adapters or device credentials.

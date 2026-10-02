# Admin Console Information Architecture

Status: Proposed for implementation  
Version: 0.1

Applicability (2026-10-02): [v0.3](device-node-services-v0.3.md) narrows initial additions to Node/service health, declaration of locality, invocation metadata, event provenance and device diagnostics. Domain management starts with permissions and minimal APIs callable by trusted entry backends, not onboarding/member-management UI. The broad navigation, role catalog, rollout and webhook screens below are historical proposals. Domain aliases do not grant access; backend authorization intersects token permissions with platform Grants.

## Purpose

The Admin Console operates and diagnoses AgenticIoT. It is not the consumer smart-home application and does not host AI chat, personal memory, or a generic automation builder.

## Primary navigation

```text
Overview
Devices
  Devices
  Device Models
  Capabilities
Edge
  Edge Nodes
  Adapters
Operations
  Commands
  Events
  Device Health
Security
  Policy Bindings
  Credentials and Trust
Audit
Settings
  API Clients
  Webhooks
```

## Pages

### Overview

Only operational exceptions and actionable summaries:

- unreachable or degraded devices;
- disconnected or unhealthy Edge nodes;
- failed and timed-out commands;
- expiring credentials or unsupported software;
- recent high-risk operations.

Avoid decorative telemetry and business KPIs.

### Devices

The list supports identity, model, domain/space references, lifecycle status, reachability, preferred Edge, and last observation. Device detail contains:

- identity and lifecycle;
- capability explorer;
- current state with freshness and source;
- bindings and connection route;
- recent commands, events, and audit history;
- configuration and software inventory.

Dangerous actions require explicit confirmation and show the exact target and capability.

### Device Models and Capabilities

Operators can inspect versions, schemas, risk classes, confirmation modes, compatible adapters, and validation errors. Published versions are immutable; changes create a new version.

### Edge Nodes

Show identity, trust posture, connection, version, capacity, assigned bindings, lease status, adapter health, and configuration-sync status.

### Commands

Expose the complete command timeline, normalized input, caller and origin references, authorization result, routing decision, receipts, evidence, timeout, and correlation trace.

### Policy Bindings

The console maps external subjects/domains/policies to platform resources and capabilities. It does not create family relationships or perform human identity proofing.

### Audit

Append-only search by actor reference, domain reference, device, command, Edge node, action, result, risk level, and time. Audit export is an administrative capability and must itself be audited.

## Access roles

Identity is external. The first console recognizes claims mapped to:

- `platform_admin`: platform configuration and lifecycle;
- `operator`: device and Edge operations;
- `security_admin`: credentials and policy bindings;
- `auditor`: read-only audit and evidence;
- `viewer`: read-only operational status.

Backend authorization is authoritative. Hiding a UI control is not an authorization mechanism.

## UX rules

1. Always distinguish cached state from fresh observation.
2. Always distinguish accepted, dispatched, acknowledged, and confirmed commands.
3. Show identifiers and trace IDs beside human labels.
4. Display destructive or high-risk scope before confirmation.
5. Never expose device secrets, bearer tokens, or raw credentials.
6. Prefer diagnosis and remediation over generic dashboards.
7. Every mutation shows its resulting resource version or command receipt.

## MVP screens

The first end-to-end release needs only:

1. device list and device detail;
2. DeviceModel/capability detail;
3. Edge node and adapter status;
4. command list and command timeline;
5. audit list.

Policy editing, software rollout, advanced event search, and dashboards follow after the virtual-device golden path is reliable.

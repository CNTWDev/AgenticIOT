# ADR 0007 — One physical device, read-only before actuation

Date: 2026-09-14  
Status: Accepted delivery boundary; device and driver selection pending

## Context

The virtual and MQTT-demo golden paths are implemented. They demonstrate transport,
journaling and evidence handling, not compatibility with a real device or secure hardware
onboarding. No manufacturer, model, firmware or physical test target has been selected.

## Decision

Proceed through three gates: confirm one target and its documented protocol; validate
read-only identity/access/observations; then separately authorize one low-risk action.
The [first-device plan](../hardware/first-device.md) contains the unfilled intake card,
configuration ownership and fault acceptance matrix. These gates are a delivery process,
not implemented lifecycle states or an activation API.

Preserve the existing simulated-only runtime. Do not turn a demo into a physical driver
by removing its simulated flag, changing a host or bridging demo topics. A device-specific
driver and any minimal API/evidence changes require the selected protocol as their basis.
Do not invent brightness support or command correlation to fit the virtual template.

Read-only means no authority to actuate: enforce that below the UI as well as at the
platform. Some protocols publish queries; their lack of side effects and separation from
control must be demonstrated. TLS, credentials and topic permissions are device-specific
requirements, not features inferred from the MQTT label. An isolated trusted gateway is
not proof of end-to-end device authentication.

Keep routing configuration and protocol secrets at Edge/operations boundaries. Store only
appropriate opaque references in the platform; the Agent cannot choose protocol endpoints
or elevate permissions. Human/domain ownership and Agent placement remain external.

Do not weaken completion semantics when hardware lacks request IDs or has multiple
controllers. A matching state is not necessarily correlated evidence. Such devices require
an explicit evidence-model decision before actuation, not adapter-generated fake IDs.

## Consequences

- User confirmation of brand/model is required before choosing the first real driver.
- Current APIs, migration head, execution permissions and simulated markers are unchanged.
- No universal provisioning portal, protocol marketplace, certificate authority or new
  message bus is introduced in anticipation of unknown hardware.
- Physical validation must cover credential revocation, stale state, duplicate/replayed
  commands, external-controller interference and operator stop procedures.
- First deliverable after target selection is read-only. Physical writes require explicit
  target/action/test-window approval and a reviewed fault report.
- Nothing in the intake checklist constitutes automated authorization or safety certification.

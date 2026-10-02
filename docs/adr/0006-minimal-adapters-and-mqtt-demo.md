# ADR 0006 — Minimal Adapter host and MQTT transport verification

Date: 2026-09-14  
Status: Accepted for local simulation only

## Decision

Keep the public Capability API and platform command queue independent of device
protocols. Add only the Edge-local operations actually needed: `read`, `invoke`,
`close`, adapter ID and an explicit `journal_atomic` flag. Do not implement a plugin
marketplace, automatic discovery, all protocols or an Agent platform.

The existing virtual adapter shares the Edge SQLite transaction. A second,
`mqtt-light-demo-v1`, uses Paho MQTT 3.1.1 over loopback to a separate simulated device.
A development-only aMQTT process supplies the broker; production platform dependencies
do not include this broker. Platform ↔ Edge remains authenticated HTTP polling;
MQTT is only Edge ↔ demo device and is not another platform message bus.

Register advertised adapter IDs, persist them in additive migration `0004_adapters`,
and bind only supported adapters in the same domain. Bindings remain immutable, including
the adapter choice. Reject removing an adapter while bindings use it. Return the selected
adapter in commands so the runtime dispatches without protocol-specific northbound APIs.
Existing registrations default to `virtual-light-v1`; old CLI imports continue working.

## Execution semantics

MQTT broker acknowledgments are not device evidence. Subscribe before publishing;
publish non-retained requests with QoS 1 and correlate full light observations by
request/command ID, thing ID and the request time window. Reject retained, stale,
malformed, oversized or mismatched responses. Missing reads never refresh cached state.

An external publish cannot participate in the Edge SQLite transaction. Persist an intent
before calling the MQTT adapter. If the process crashes before recording the result, or
no valid device evidence arrives, record `failed / execution_uncertain`. Do not automatically
republish. This deliberately trades availability for avoiding unproven re-execution and
can report uncertainty even when a crash occurred just before the actual send.

The simulated device persists command ID, request fingerprint-equivalent canonical JSON,
result and simulated state in one transaction. QoS duplicate deliveries return identical
evidence; conflicting reuse is rejected. This proves simulator deduplication, not exactly-once
physical effects. Once the Edge records a result, API retries replay that exact result.
An uncertain result is immutable in this slice; later ordinary state reports cannot
retroactively prove execution. Manual reconciliation is still required.

## Boundaries and consequences

- All execution remains `simulated: true`; neither MQTT nor a valid topic proves device identity.
- Broker addresses and namespace configuration stay on Edge, never in Agent requests or UI credentials.
- The demo is unauthenticated, fixed to 127.0.0.1 and a dedicated topic prefix. A namespace is not an ACL. It must not be bridged/exposed to a household or production broker.
- Light schemas/mappings are fixed. No arbitrary MQTT payload templates, retained actuations, automatic reconnect/retry or background subscriptions.
- No TLS, hardware onboarding, protocol credential rotation, fencing, offline authorization, dynamic loading or general device compatibility claim.
- Downgrading 0004 refuses while MQTT bindings exist: old code must not reinterpret them as virtual devices.
- Sequential bounded exchanges suit a small local demo, not a throughput/latency SLA. Journal retention and compaction remain pending.

Next: select one real device and its protocol contract, then define secure enrollment,
topic permissions, idempotency/readback semantics and a fault acceptance matrix before
granting a physical driver authority. Agent placement and person/domain ownership remain
outside this platform.

## References

- [Paho Python documentation and delivery limitations](https://eclipse.dev/paho/clients/python/docs/)
- [Paho client API](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html)
- [aMQTT broker configuration](https://amqtt.readthedocs.io/en/v0.11.3/references/broker_config/)

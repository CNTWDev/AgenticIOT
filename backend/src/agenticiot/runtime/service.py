import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from agenticiot.nodes.models import NodeCredential
from agenticiot.registry.models import Device, DeviceModel
from agenticiot.registry.schemas import DeviceView
from agenticiot.registry.service import RegistryService
from agenticiot.runtime.models import (
    Binding,
    Command,
    EdgeNode,
    Observation,
    Receipt,
    StateProjection,
)
from agenticiot.runtime.schemas import CommandView, ReceiptView
from agenticiot.security import APIError

ACTION_PROPERTIES = {"set_power": "power", "set_brightness": "brightness"}
LIGHT_SCHEMAS = {
    "power": {"type": "boolean"},
    "brightness": {"type": "integer", "minimum": 0, "maximum": 100},
}


def now():
    return datetime.now(UTC)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def freshness(observed_at):
    age = (now() - observed_at).total_seconds()
    return "fresh" if age <= 10 else "aging" if age <= 30 else "stale"


def device_view(session, device):
    state = session.get(StateProjection, device.id)
    reachability = (
        "unknown"
        if state is None
        else "offline"
        if freshness(state.observed_at) == "stale"
        else "online"
    )
    return DeviceView.model_validate(device).model_copy(update={"reachability": reachability})


class RuntimeService(RegistryService):
    def locked(self, kind, resource_id):
        resource = self.session.scalar(
            select(kind)
            .where(kind.id == resource_id, kind.domain_ref == self.domain)
            .with_for_update()
        )
        if resource is None:
            raise APIError(404, "resource_not_found", "Resource not found")
        return resource

    def binding(self, thing_id):
        self.get(Device, thing_id)
        return self.session.scalar(
            select(Binding).where(Binding.thing_id == thing_id, Binding.domain_ref == self.domain)
        )

    def bind(self, thing_id, data):
        device = self.locked(Device, thing_id)
        edge = self.locked(EdgeNode, data.edge_id)
        credential = self.session.get(NodeCredential, edge.id)
        if credential is not None and not credential.enabled:
            raise APIError(409, "node_disabled", "Node is disabled")
        if data.adapter not in edge.adapters:
            raise APIError(422, "unsupported_adapter", "Edge has not advertised this adapter")
        existing = self.binding(thing_id)
        if existing:
            if existing.edge_id != edge.id or existing.adapter != data.adapter:
                raise APIError(409, "binding_conflict", "Rebinding is not supported in this slice")
            return existing
        count = self.session.scalar(
            select(func.count()).select_from(Binding).where(Binding.edge_id == edge.id)
        )
        if count >= 200:
            raise APIError(409, "edge_capacity", "Virtual runtime supports at most 200 bindings")
        model = self.get(DeviceModel, device.model_id)
        for action, prop in ACTION_PROPERTIES.items():
            spec = model.actions.get(action, {})
            expected_input = {
                "type": "object",
                "properties": {"value": LIGHT_SCHEMAS[prop]},
                "required": ["value"],
                "additionalProperties": False,
            }
            if (
                model.properties.get(prop, {}).get("schema") != LIGHT_SCHEMAS[prop]
                or not model.properties[prop].get("readable", True)
                or spec.get("input_schema") != expected_input
                or spec.get("risk") != "low"
                or spec.get("confirmation") != "observed_state"
                or spec.get("offline_policy") == "local_only"
            ):
                raise APIError(
                    422, "unsupported_model", "Use the virtual-light-v1 capability template"
                )
        if device.lifecycle_status != "commissioning":
            raise APIError(409, "device_not_eligible", "Device is not commissioning")
        binding = Binding(
            id=uuid4().hex,
            domain_ref=self.domain,
            thing_id=thing_id,
            edge_id=edge.id,
            adapter=data.adapter,
        )
        device.lifecycle_status = "active"
        self.session.add(binding)
        self.audit("device.bound", thing_id)
        self.session.commit()
        return binding

    def receipt(self, command, stage, *, evidence=None, error=None, executor=None):
        # Every caller holds the command row lock (or owns its uncommitted insert).
        previous = self.session.scalars(
            select(Receipt.sequence).where(Receipt.command_id == command.id)
        ).all()
        self.session.add(
            Receipt(
                command_id=command.id,
                sequence=max(previous, default=0) + 1,
                stage=stage,
                executor_ref=executor,
                evidence=evidence or {},
                error_code=error,
                trace_id=command.trace_id,
            )
        )
        command.stage = stage
        self.session.flush()

    def settle(self, command):
        if command.deadline <= now() and command.stage in ("accepted", "dispatched"):
            stage = "expired" if command.stage == "accepted" else "timed_out"
            self.receipt(command, stage, error="deadline_elapsed")
            self.audit(f"command.{stage}", command.id)

    def command_view(self, command):
        status = {
            "accepted": "accepted",
            "dispatched": "running",
            "confirmed": "succeeded",
            "expired": "expired",
            "timed_out": "unknown",
        }.get(command.stage, "failed")
        receipts = self.session.scalars(
            select(Receipt).where(Receipt.command_id == command.id).order_by(Receipt.sequence)
        ).all()
        if command.stage == "failed" and any(
            r.error_code == "execution_uncertain" for r in receipts
        ):
            status = "unknown"
        return CommandView(
            id=command.id,
            thing_id=command.thing_id,
            adapter=self.session.get(Binding, command.binding_id).adapter,
            action=command.action,
            input=command.input,
            status=status,
            created_at=command.created_at,
            deadline=command.deadline,
            trace_id=command.trace_id,
            receipts=[ReceiptView.model_validate(item) for item in receipts],
        )

    def invoke(self, thing_id, action, data, key):
        from agenticiot.access.service import LOCAL_CLIENT, LOCAL_ISSUER

        # Preserve historical development keys; external entries are independently namespaced.
        if (self.principal.issuer, self.principal.client_id) != (LOCAL_ISSUER, LOCAL_CLIENT):
            key = fingerprint([self.principal.issuer, self.principal.client_id, key])
        # Serializes same-device retries before checking the unique caller/key index.
        device = self.locked(Device, thing_id)
        try:
            request_hash = fingerprint(
                {"thing_id": thing_id, "action": action, **data.model_dump(mode="json")}
            )
        except (ValueError, TypeError):
            raise APIError(422, "invalid_request", "Input must be finite JSON") from None
        previous = self.session.scalar(
            select(Command)
            .where(
                Command.domain_ref == self.domain,
                Command.subject_ref == self.principal.subject_ref,
                Command.idempotency_key == key,
            )
            .with_for_update()
        )
        if previous:
            if previous.request_hash != request_hash:
                raise APIError(409, "idempotency_conflict", "Key was used for a different request")
            self.settle(previous)
            self.session.commit()
            return self.command_view(previous)
        binding = self.binding(thing_id)
        if not binding or device.lifecycle_status != "active":
            raise APIError(409, "device_not_bound", "An active virtual binding is required")
        credential = self.session.get(NodeCredential, binding.edge_id)
        if credential is not None and not credential.enabled:
            raise APIError(409, "node_disabled", "Node is disabled")
        model = self.get(DeviceModel, device.model_id)
        spec = model.actions.get(action)
        if action not in ACTION_PROPERTIES or not spec:
            raise APIError(422, "unsupported_action", "Only virtual-light actions are supported")
        if (
            spec["risk"] != "low"
            or spec["confirmation"] != "observed_state"
            or spec.get("offline_policy") == "local_only"
        ):
            raise APIError(403, "policy_denied", "Action is outside this execution policy")
        if not Draft202012Validator(spec["input_schema"]).is_valid(data.input):
            raise APIError(422, "invalid_action_input", "Input does not match the action schema")
        deadline = data.deadline or now() + timedelta(seconds=30)
        if not now() < deadline <= now() + timedelta(minutes=5):
            raise APIError(422, "invalid_deadline", "Deadline must be within the next five minutes")
        command = Command(
            id=uuid4().hex,
            domain_ref=self.domain,
            subject_ref=self.principal.subject_ref,
            thing_id=thing_id,
            binding_id=binding.id,
            edge_id=binding.edge_id,
            action=action,
            input=data.input,
            idempotency_key=key,
            request_hash=request_hash,
            deadline=deadline,
            trace_id=self.trace_id,
        )
        self.session.add(command)
        self.session.flush()
        self.receipt(
            command,
            "accepted",
            evidence={"policy": "local-operator-low-risk-v1", "simulated": True},
        )
        self.audit("command.accepted", command.id)
        # The command row itself is the durable pull queue; there is no publish-before-commit gap.
        self.session.commit()
        return self.command_view(command)

    def read_command(self, command_id):
        command = self.locked(Command, command_id)
        self.settle(command)
        self.session.commit()
        return self.command_view(command)

    def commands(self, thing_id):
        self.get(Device, thing_id)
        commands = self.session.scalars(
            select(Command)
            .where(Command.thing_id == thing_id, Command.domain_ref == self.domain)
            .order_by(Command.created_at.desc(), Command.id)
            .limit(20)
            .with_for_update()
        ).all()
        for command in commands:
            self.settle(command)
        self.session.commit()
        return [self.command_view(command) for command in commands]

    def state(self, thing_id):
        self.get(Device, thing_id)
        state = self.session.get(StateProjection, thing_id)
        properties = (
            {}
            if state is None
            else {
                prop: {
                    "value": value,
                    "source_ref": state.source_ref,
                    "observed_at": state.observed_at,
                    "received_at": state.received_at,
                    "source_version": str(state.source_sequence),
                    "freshness": freshness(state.observed_at),
                    "quality": "good",
                }
                for prop, value in state.values.items()
            }
        )
        return {"thing_id": thing_id, "properties": properties, "simulated": True}


class EdgeService:
    def __init__(self, session, principal, trace_id):
        self.session, self.principal, self.trace_id = session, principal, trace_id
        self.domain = principal.domain_ref
        self.storage = RuntimeService(session, principal, trace_id)
        self.edge_ref = principal.edge_ref

    def audit(self, *args, **kwargs):
        return self.storage.audit(*args, **kwargs)

    def locked(self, *args, **kwargs):
        return self.storage.locked(*args, **kwargs)

    def binding(self, *args, **kwargs):
        return self.storage.binding(*args, **kwargs)

    def receipt(self, *args, **kwargs):
        return self.storage.receipt(*args, **kwargs)

    def settle(self, *args, **kwargs):
        return self.storage.settle(*args, **kwargs)

    def command_view(self, *args, **kwargs):
        return self.storage.command_view(*args, **kwargs)

    def edge(self):
        edge = self.session.scalar(
            select(EdgeNode)
            .where(EdgeNode.domain_ref == self.domain, EdgeNode.edge_ref == self.edge_ref)
            .with_for_update()
        )
        if edge is None:
            raise APIError(409, "edge_not_registered", "Register the Edge before polling")
        edge.last_seen_at = now()
        return edge

    def register(self, data):
        edge = self.session.scalar(
            select(EdgeNode)
            .where(EdgeNode.domain_ref == self.domain, EdgeNode.edge_ref == self.edge_ref)
            .with_for_update()
        )
        if edge is None:
            edge = EdgeNode(
                id=uuid4().hex,
                domain_ref=self.domain,
                edge_ref=self.edge_ref,
                **data.model_dump(),
                last_seen_at=now(),
            )
            self.session.add(edge)
            self.audit("edge.registered", edge.id)
        else:
            required = set(
                self.session.scalars(
                    select(Binding.adapter).where(Binding.edge_id == edge.id)
                ).all()
            )
            if not required.issubset(data.adapters):
                raise APIError(
                    409, "adapter_in_use", "Cannot remove an adapter with active bindings"
                )
            edge.title, edge.version, edge.last_seen_at = data.title, data.version, now()
            edge.adapters = data.adapters
        self.session.commit()
        return edge

    def assigned(self):
        edge = self.edge()
        bindings = self.session.scalars(
            select(Binding)
            .where(Binding.edge_id == edge.id, Binding.domain_ref == self.domain)
            .order_by(Binding.id)
            .limit(201)
        ).all()
        if len(bindings) > 200:
            raise APIError(409, "edge_capacity", "Virtual runtime supports at most 200 bindings")
        self.session.commit()
        return bindings

    def claim(self):
        edge = self.edge()
        commands = self.session.scalars(
            select(Command)
            .where(Command.edge_id == edge.id, Command.stage.in_(["accepted", "dispatched"]))
            .order_by(Command.created_at, Command.id)
            .with_for_update()
        ).all()
        selected = None
        for command in commands:
            self.settle(command)
            if selected is None and command.stage in ("accepted", "dispatched"):
                selected = command
        if selected is not None and selected.stage == "accepted":
            self.receipt(selected, "dispatched", executor=edge.edge_ref)
        self.session.commit()
        return None if selected is None else self.command_view(selected)

    def observe(self, edge, thing_id, data, command_id=None):
        binding = self.binding(thing_id)
        if not binding or binding.edge_id != edge.id:
            raise APIError(404, "resource_not_found", "Assigned device not found")
        envelope = {"thing_id": thing_id, "command_id": command_id, **data.model_dump(mode="json")}
        digest = fingerprint(envelope)
        old = self.session.scalar(
            select(Observation).where(
                Observation.edge_id == edge.id, Observation.source_sequence == data.source_sequence
            )
        )
        if old:
            if old.request_hash != digest:
                raise APIError(
                    409, "sequence_conflict", "Sequence already used for different evidence"
                )
            return old
        if data.source_sequence <= edge.last_sequence:
            raise APIError(409, "sequence_conflict", "Observation sequence must increase")
        if data.observed_at > now() + timedelta(seconds=5):
            raise APIError(422, "invalid_observation_time", "Observation is in the future")
        observation = Observation(
            id=uuid4().hex,
            domain_ref=self.domain,
            thing_id=thing_id,
            edge_id=edge.id,
            command_id=command_id,
            source_sequence=data.source_sequence,
            source_ref=edge.edge_ref,
            observed_at=data.observed_at,
            values=data.values.model_dump(),
            request_hash=digest,
        )
        self.session.add(observation)
        edge.last_sequence = data.source_sequence
        self.session.flush()
        state = self.session.get(StateProjection, thing_id)
        from agenticiot.events import capture_change

        capture_change(self.session, self.domain, state, observation)
        if state is None:
            state = StateProjection(thing_id=thing_id)
            self.session.add(state)
        if state.observed_at is None or observation.observed_at >= state.observed_at:
            for name in ("observed_at", "received_at", "source_ref", "source_sequence", "values"):
                setattr(state, name, getattr(observation, name))
            state.observation_id = observation.id
        return observation

    def upload(self, thing_id, data):
        observation = self.observe(self.edge(), thing_id, data)
        self.session.commit()
        return {"observation_id": observation.id, "source_sequence": observation.source_sequence}

    def result(self, command_id, data):
        edge = self.edge()
        command = self.locked(Command, command_id)
        if command.edge_id != edge.id:
            raise APIError(404, "resource_not_found", "Assigned command not found")
        digest = fingerprint(data.model_dump(mode="json"))
        if command.result_hash:
            if command.result_hash != digest:
                raise APIError(409, "result_conflict", "Result is immutable")
            return self.command_view(command)
        self.settle(command)
        if command.stage not in ("dispatched", "timed_out"):
            raise APIError(409, "invalid_command_stage", "Command was not dispatched")
        if data.outcome == "failed":
            self.receipt(command, "failed", executor=edge.edge_ref, error=data.error_code)
        else:
            dispatched_at = self.session.scalar(
                select(Receipt.occurred_at).where(
                    Receipt.command_id == command.id, Receipt.stage == "dispatched"
                )
            )
            if data.observed_at < dispatched_at:
                raise APIError(
                    422, "invalid_observation_time", "Completion evidence predates dispatch"
                )
            observation = self.observe(edge, command.thing_id, data, command.id)
            matched = (
                observation.values[ACTION_PROPERTIES[command.action]] == command.input["value"]
            )
            self.receipt(command, "acknowledged", executor=edge.edge_ref)
            self.receipt(
                command,
                "confirmed" if matched else "failed",
                executor=edge.edge_ref,
                evidence={
                    "kind": "state_observation",
                    "observation_id": observation.id,
                    "simulated": True,
                },
                error=None if matched else "confirmation_mismatch",
            )
        command.result_hash = digest
        self.audit(f"command.{command.stage}", command.id)
        self.session.commit()
        return self.command_view(command)

"""MQTT 3.1.1 demo light wire contract; loopback only, not a hardware driver."""

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import paho.mqtt.client as mqtt
from pydantic import AwareDatetime, ValidationError, model_validator

from agenticiot.adapters import AdapterError, light_change
from agenticiot.registry.schemas import ResourceID, SchemaModel
from agenticiot.runtime.schemas import LightValues


class LightRequest(SchemaModel):
    version: Literal[1] = 1
    simulated: Literal[True] = True
    request_id: ResourceID
    thing_id: ResourceID
    operation: Literal["read", "invoke"]
    issued_at: AwareDatetime
    deadline: AwareDatetime
    action: Literal["set_power", "set_brightness"] | None = None
    input: dict | None = None

    @model_validator(mode="after")
    def check_operation(self):
        if self.deadline <= self.issued_at:
            raise ValueError("Invalid time window")
        if self.operation == "invoke":
            if self.input is None:
                raise ValueError("Missing input")
            try:
                light_change(self.model_dump())
            except AdapterError as error:
                raise ValueError("Invalid light command") from error
        elif self.action is not None or self.input is not None:
            raise ValueError("Read cannot carry an action")
        return self


class LightResponse(SchemaModel):
    version: Literal[1] = 1
    simulated: Literal[True] = True
    request_id: ResourceID
    thing_id: ResourceID
    observed_at: AwareDatetime
    values: LightValues


def topic_root(namespace, thing_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", namespace):
        raise ValueError("Namespace must be a single safe topic segment")
    if not re.fullmatch(r"[a-f0-9]{32}", thing_id):
        raise ValueError("Thing ID must be a platform resource ID")
    return f"agenticiot/demo/{namespace}/{thing_id}"


def decode_response(payload, retained, request):
    if retained or len(payload) > 4096:
        return None
    try:
        response = LightResponse.model_validate_json(payload)
    except (ValidationError, ValueError):
        return None
    if (
        response.request_id != request.request_id
        or response.thing_id != request.thing_id
        or not request.issued_at <= response.observed_at <= min(request.deadline, datetime.now(UTC))
    ):
        return None
    return {"values": response.values.model_dump(), "observed_at": response.observed_at.isoformat()}


class MQTTLightAdapter:
    id = "mqtt-light-demo-v1"
    journal_atomic = False

    def __init__(self, port, namespace="local-demo", *, timeout=2.0):
        topic_root(namespace, "0" * 32)
        if not 1 <= port <= 65535 or not 0 < timeout <= 5:
            raise ValueError("Invalid loopback MQTT port or timeout")
        self.port, self.namespace, self.timeout = port, namespace, timeout

    def exchange(self, request):
        root = topic_root(self.namespace, request.thing_id)
        remaining = min(self.timeout, (request.deadline - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            raise AdapterError("MQTT request expired")
        until = time.monotonic() + remaining
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
        client.connect_timeout = remaining
        ready, response = False, None

        def connected(client, userdata, flags, reason_code, properties):
            if not reason_code.is_failure:
                client.subscribe(root + "/response", qos=1)

        def subscribed(client, userdata, mid, reason_codes, properties):
            nonlocal ready
            ready = bool(reason_codes) and all(not code.is_failure for code in reason_codes)

        def received(client, userdata, message):
            nonlocal response
            if message.topic == root + "/response":
                decoded = decode_response(message.payload, message.retain, request)
                if decoded is not None:
                    response = decoded

        client.on_connect, client.on_subscribe, client.on_message = connected, subscribed, received
        sent = False
        try:
            client.connect("127.0.0.1", self.port, keepalive=10)
            while time.monotonic() < until:
                if (
                    client.loop(timeout=min(0.05, max(0, until - time.monotonic())))
                    != mqtt.MQTT_ERR_SUCCESS
                ):
                    break
                if ready and not sent:
                    # SUBACK precedes publish. PUBACK alone is never completion evidence.
                    info = client.publish(
                        root + "/request", request.model_dump_json(), qos=1, retain=False
                    )
                    sent = True
                    if info.rc != mqtt.MQTT_ERR_SUCCESS:
                        break
                if sent and response is not None:
                    return response
        except (OSError, RuntimeError):
            pass
        finally:
            client.disconnect()
        raise AdapterError("MQTT observation unavailable; execution may be uncertain")

    def read(self, thing_id):
        now = datetime.now(UTC)
        return self.exchange(
            LightRequest(
                request_id=uuid4().hex,
                thing_id=thing_id,
                operation="read",
                issued_at=now,
                deadline=now + timedelta(seconds=self.timeout),
            )
        )

    def invoke(self, command):
        light_change(command)
        try:
            request = LightRequest(
                request_id=command["id"],
                thing_id=command["thing_id"],
                operation="invoke",
                issued_at=datetime.now(UTC),
                deadline=command["deadline"],
                action=command["action"],
                input=command["input"],
            )
        except ValidationError:
            raise AdapterError("Invalid or expired MQTT command") from None
        return self.exchange(request)

    def close(self):
        pass  # Each bounded exchange closes its own connection; no auto-reconnect/replay.

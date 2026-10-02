import json
import selectors
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from agenticiot.adapters import AdapterError, VirtualLightAdapter
from agenticiot.edge import EdgeRuntime
from agenticiot.mqtt_adapter import LightRequest, MQTTLightAdapter, decode_response
from agenticiot.mqtt_demo import DemoLight
from paho.mqtt.publish import single


@contextmanager
def demo_process(*args):
    process = subprocess.Popen(
        [sys.executable, "-m", "agenticiot.mqtt_demo", *map(str, args)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            until = time.monotonic() + 10
            output = []
            while time.monotonic() < until:
                if selector.select(timeout=0.2):
                    line = process.stdout.readline()
                    output.append(line.decode())
                    if line.startswith(b"READY"):
                        yield process
                        return
                    if not line:
                        break
            pytest.fail("Demo process did not become ready: " + "".join(output))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


@pytest.fixture
def mqtt_broker():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with demo_process("broker", "--port", port):
        yield port


def command(thing_id=None):
    return {
        "id": uuid4().hex,
        "thing_id": thing_id or uuid4().hex,
        "adapter": "mqtt-light-demo-v1",
        "action": "set_power",
        "input": {"value": True},
        "simulated": True,
        "deadline": (datetime.now(UTC) + timedelta(seconds=20)).isoformat(),
    }


@pytest.mark.parametrize("adapter_name", ["virtual", "mqtt"])
def test_shared_adapter_read_invoke_contract(adapter_name, mqtt_broker, tmp_path):
    intent = command()
    worker = EdgeRuntime(tmp_path / "edge.sqlite3", None)
    try:
        with demo_process(
            "device",
            "--port",
            mqtt_broker,
            "--thing-id",
            intent["thing_id"],
            "--data-dir",
            tmp_path / "device",
        ):
            adapter = (
                VirtualLightAdapter(worker.db)
                if adapter_name == "virtual"
                else MQTTLightAdapter(mqtt_broker)
            )
            with worker.db:
                before = adapter.read(intent["thing_id"])
                after = adapter.invoke(intent)
                current = adapter.read(intent["thing_id"])
            assert before["values"] == {"power": False, "brightness": 0}
            assert after["values"] == current["values"] == {"power": True, "brightness": 0}
            assert datetime.fromisoformat(after["observed_at"]) >= datetime.fromisoformat(
                before["observed_at"]
            )
            adapter.close()
    finally:
        worker.close()


@pytest.mark.parametrize(
    "bad",
    ["retained", "wrong_request", "wrong_thing", "stale", "future", "invalid_value", "oversized"],
)
def test_untrusted_mqtt_evidence_is_ignored(bad):
    now = datetime.now(UTC)
    request = LightRequest(
        request_id=uuid4().hex,
        thing_id=uuid4().hex,
        operation="read",
        issued_at=now - timedelta(seconds=1),
        deadline=now + timedelta(seconds=2),
    )
    response = {
        "request_id": request.request_id,
        "thing_id": request.thing_id,
        "observed_at": now.isoformat(),
        "values": {"power": True, "brightness": 50},
    }
    assert decode_response(json.dumps(response).encode(), False, request) is not None
    if bad == "wrong_request":
        response["request_id"] = uuid4().hex
    elif bad == "wrong_thing":
        response["thing_id"] = uuid4().hex
    elif bad in ("stale", "future"):
        response["observed_at"] = (
            now + timedelta(seconds=-10 if bad == "stale" else 10)
        ).isoformat()
    elif bad == "invalid_value":
        response["values"]["power"] = "true"
    payload = b"x" * 4097 if bad == "oversized" else json.dumps(response).encode()
    assert decode_response(payload, bad == "retained", request) is None


def test_device_dedup_survives_restart_and_rejects_conflicts(tmp_path):
    intent = command()
    request = LightRequest(
        request_id=intent["id"],
        thing_id=intent["thing_id"],
        operation="invoke",
        issued_at=datetime.now(UTC),
        deadline=intent["deadline"],
        action=intent["action"],
        input=intent["input"],
    )
    path = tmp_path / "device.sqlite3"
    device = DemoLight(path, intent["thing_id"])
    assert device.handle(request.model_dump_json(), retained=True) is None
    first = device.handle(request.model_dump_json())
    device.close()
    device = DemoLight(path, intent["thing_id"])
    try:
        assert device.handle(request.model_dump_json()) == first
        assert (
            device.handle(request.model_copy(update={"input": {"value": False}}).model_dump_json())
            is None
        )
        assert device.db.execute("SELECT count(*) FROM applied").fetchone()[0] == 1
        assert (
            json.loads(device.db.execute("SELECT state FROM light").fetchone()[0])["power"] is True
        )
    finally:
        device.close()


def test_puback_without_device_evidence_is_not_success(mqtt_broker, tmp_path):
    adapter = MQTTLightAdapter(mqtt_broker, timeout=0.3)
    with pytest.raises(AdapterError):
        adapter.read(uuid4().hex)
    uploaded = []
    worker = EdgeRuntime(
        tmp_path / "edge.sqlite3",
        lambda method, path, data: uploaded.append(data),
        adapters=[adapter],
    )
    try:
        intent = command()
        worker.execute(intent)
        worker.flush()
        worker.execute(intent)
        worker.flush()
        assert uploaded == [{"outcome": "failed", "error_code": "execution_uncertain"}] * 2
    finally:
        worker.close()


def test_retained_broker_snapshot_is_not_execution_evidence(mqtt_broker):
    now = datetime.now(UTC)
    request = LightRequest(
        request_id=uuid4().hex,
        thing_id=uuid4().hex,
        operation="read",
        issued_at=now - timedelta(seconds=1),
        deadline=now + timedelta(seconds=5),
    )
    payload = json.dumps(
        {
            "request_id": request.request_id,
            "thing_id": request.thing_id,
            "observed_at": now.isoformat(),
            "values": {"power": True, "brightness": 0},
        }
    )
    single(
        f"agenticiot/demo/local-demo/{request.thing_id}/response",
        payload,
        qos=1,
        retain=True,
        hostname="127.0.0.1",
        port=mqtt_broker,
    )
    with pytest.raises(AdapterError):
        MQTTLightAdapter(mqtt_broker, timeout=0.3).exchange(request)


def test_broker_connection_failure_never_returns_synthetic_state():
    # A reserved but non-listening local port deterministically rejects the connection.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        adapter = MQTTLightAdapter(reserved.getsockname()[1], timeout=0.3)
        with pytest.raises(AdapterError):
            adapter.read(uuid4().hex)


def test_crash_after_external_effect_does_not_reexecute(tmp_path):
    class CrashAdapter:
        id, journal_atomic, calls = "mqtt-light-demo-v1", False, 0

        def invoke(self, command):
            self.calls += 1
            raise SystemExit("Simulated crash after publish, before result commit")

        def close(self):
            pass

    adapter = CrashAdapter()
    path, intent, uploaded = tmp_path / "edge.sqlite3", command(), []
    worker = EdgeRuntime(path, None, adapters=[adapter])
    with pytest.raises(SystemExit):
        worker.execute(intent)
    worker.close()
    worker = EdgeRuntime(path, lambda method, path, data: uploaded.append(data), adapters=[adapter])
    try:
        worker.execute(intent)
        worker.flush()
        assert adapter.calls == 1
        assert uploaded == [{"outcome": "failed", "error_code": "execution_uncertain"}]
    finally:
        worker.close()


@pytest.mark.parametrize("namespace", ["+/lamp", "#", "home/elsewhere", ""])
def test_mqtt_topics_cannot_escape_local_namespace(namespace):
    with pytest.raises(ValueError):
        MQTTLightAdapter(18883, namespace)

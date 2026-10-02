import pytest
from agenticiot.config import EdgeClient
from agenticiot.edge import EdgeRuntime
from agenticiot.mqtt_adapter import MQTTLightAdapter
from agenticiot.runtime.models import (
    Binding,
    Command,
    EdgeNode,
    Observation,
    Receipt,
    StateProjection,
)
from alembic import command as migration
from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from test_mqtt import demo_process, mqtt_broker  # noqa: F401
from test_registry import TOKEN_B, publish, register, registry_client  # noqa: F401
from test_runtime import EDGE_TOKEN, virtual_model


@pytest.mark.integration
def test_mqtt_platform_binding_evidence_and_failure_boundaries(request, tmp_path):
    client, domain, _ = request.getfixturevalue("registry_client")
    port = request.getfixturevalue("mqtt_broker")
    client.app.state.settings.edge_clients = [
        EdgeClient(token=EDGE_TOKEN, edge_ref="edge:mqtt", domain_ref=domain)
    ]

    def channel(method, path, data=None):
        response = client.request(
            method, "/v1" + path, json=data, headers={"Authorization": f"Bearer {EDGE_TOKEN}"}
        )
        assert response.status_code == 200, response.text
        return response.json()

    adapter = MQTTLightAdapter(port, timeout=0.3)
    worker = EdgeRuntime(tmp_path / "edge.sqlite3", channel, adapters=[adapter])
    try:
        virtual_only = channel("POST", "/edge/register", {"title": "Before adapter upgrade"})
        model = publish(client, **virtual_model())
        device = register(client, model).json()
        path = f"/v1/management/devices/{device['id']}/binding"
        unsupported = client.put(path, json={"edge_id": virtual_only["id"], "adapter": adapter.id})
        assert unsupported.status_code == 422
        assert unsupported.json()["code"] == "unsupported_adapter"
        worker.tick()
        edge = client.get("/v1/management/edges").json()["items"][0]
        assert edge["adapters"] == ["virtual-light-v1", "mqtt-light-demo-v1"]
        payload = {"edge_id": edge["id"], "adapter": adapter.id}
        assert (
            client.put(
                path, json=payload, headers={"Authorization": f"Bearer {TOKEN_B}"}
            ).status_code
            == 404
        )
        assert client.put(path, json=payload).json()["adapter"] == adapter.id
        assert client.put(path, json=payload).status_code == 200
        assert client.put(path, json={"edge_id": edge["id"]}).status_code == 409
        assert (
            client.post(
                "/v1/edge/register",
                json={"title": "Drop MQTT"},
                headers={"Authorization": f"Bearer {EDGE_TOKEN}"},
            ).status_code
            == 409
        )
        with pytest.raises(RuntimeError, match="forward-only"):
            migration.downgrade(Config("alembic.ini"), "0003_runtime")
        assert client.get("/health/ready").status_code == 200
        with demo_process(
            "device", "--port", port, "--thing-id", device["id"], "--data-dir", tmp_path / "device"
        ):
            worker.tick()
            before = client.get(f"/v1/things/{device['id']}/state").json()
            assert before["properties"]["power"]["value"] is False
            accepted = client.post(
                f"/v1/things/{device['id']}/actions/set_power",
                headers={"Idempotency-Key": "mqtt-on"},
                json={"input": {"value": True}},
            ).json()
            assert accepted["adapter"] == adapter.id
            worker.tick()
            completed = client.get(f"/v1/commands/{accepted['id']}").json()
            assert completed["status"] == "succeeded"
            assert [r["stage"] for r in completed["receipts"]] == [
                "accepted",
                "dispatched",
                "acknowledged",
                "confirmed",
            ]
            assert completed["receipts"][-1]["evidence"]["observation_id"]
            state = client.get(f"/v1/things/{device['id']}/state").json()
            assert state["properties"]["power"]["value"] is True
        # Broker still delivers PUBACKs, but the device has stopped. Do not refresh stale state.
        pending = client.post(
            f"/v1/things/{device['id']}/actions/set_power",
            headers={"Idempotency-Key": "mqtt-off"},
            json={"input": {"value": False}},
        ).json()
        worker.tick()
        failed = client.get(f"/v1/commands/{pending['id']}").json()
        assert failed["status"] == "unknown"
        assert failed["receipts"][-1]["error_code"] == "execution_uncertain"
        unchanged = client.get(f"/v1/things/{device['id']}/state").json()
        assert (
            unchanged["properties"]["power"]["observed_at"]
            == state["properties"]["power"]["observed_at"]
        )
        assert unchanged["properties"]["power"]["value"] is True
    finally:
        worker.close()
        # Remove only this test's runtime data so migration round-trip tests remain independent.
        with Session(client.app.state.engine) as session, session.begin():
            commands = select(Command.id).where(Command.domain_ref == domain)
            things = select(Binding.thing_id).where(Binding.domain_ref == domain)
            session.execute(delete(Receipt).where(Receipt.command_id.in_(commands)))
            session.execute(delete(StateProjection).where(StateProjection.thing_id.in_(things)))
            for kind in (Observation, Command, Binding, EdgeNode):
                session.execute(delete(kind).where(kind.domain_ref == domain))

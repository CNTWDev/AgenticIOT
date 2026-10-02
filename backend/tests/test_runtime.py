from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from agenticiot.config import EdgeClient
from agenticiot.runtime.models import Command, StateProjection
from agenticiot.runtime.service import ACTION_PROPERTIES, LIGHT_SCHEMAS
from agenticiot.virtual_edge import VirtualEdge
from alembic import command as migration
from alembic.config import Config
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session
from test_registry import TOKEN_B, TOKEN_VIEWER, publish, register, registry_client  # noqa: F401
from test_registry_schema import light_definition

EDGE_TOKEN = "edge-a-" + "e" * 32
OTHER_EDGE = "edge-other-" + "o" * 32
FOREIGN_EDGE = "edge-foreign-" + "f" * 32


def virtual_model():
    data = light_definition()
    for action, prop in ACTION_PROPERTIES.items():
        data["properties"][prop] = {"schema": LIGHT_SCHEMAS[prop], "readable": True}
        data["actions"][action] = {
            "input_schema": {
                "type": "object",
                "properties": {"value": LIGHT_SCHEMAS[prop]},
                "required": ["value"],
                "additionalProperties": False,
            },
            "risk": "low",
            "confirmation": "observed_state",
            "offline_policy": "online_required",
        }
    return data


@pytest.fixture
def execution(request):
    client, domain, foreign_domain = request.getfixturevalue("registry_client")
    client.app.state.settings.edge_clients = [
        EdgeClient(token=token, edge_ref=ref, domain_ref=scope)
        for token, ref, scope in [
            (EDGE_TOKEN, "edge:local", domain),
            (OTHER_EDGE, "edge:other", domain),
            (FOREIGN_EDGE, "edge:foreign", foreign_domain),
        ]
    ]
    edges = [
        client.post(
            "/v1/edge/register",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Test Edge"},
        ).json()
        for token in [EDGE_TOKEN, OTHER_EDGE, FOREIGN_EDGE]
    ]
    model = publish(client, **virtual_model())
    device = register(client, model).json()
    response = client.put(
        f"/v1/management/devices/{device['id']}/binding", json={"edge_id": edges[0]["id"]}
    )
    assert response.status_code == 200, response.text
    return client, device, edges


def edge_post(client, path, data=None, token=EDGE_TOKEN):
    return client.post(
        "/v1/edge" + path, headers={"Authorization": f"Bearer {token}"}, json=data or {}
    )


def invoke(client, device, value=True, key="intent-1", **kwargs):
    return client.post(
        f"/v1/things/{device['id']}/actions/set_power",
        headers={"Idempotency-Key": key},
        json={"input": {"value": value}, **kwargs},
    )


def observation(sequence=1, power=True, age=0):
    return {
        "source_sequence": sequence,
        "observed_at": (datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
        "values": {"power": power, "brightness": 0},
    }


@pytest.mark.integration
def test_durable_acceptance_confirmation_and_exact_retries(execution):
    client, device, _ = execution
    response = invoke(client, device)
    assert response.status_code == 202, response.text
    command = response.json()
    assert command["status"] == "accepted"
    assert [r["stage"] for r in command["receipts"]] == ["accepted"]
    assert invoke(client, device).json()["id"] == command["id"]
    assert invoke(client, device, value=False).status_code == 409
    claimed = edge_post(client, "/commands/claim").json()
    assert claimed["status"] == "running"
    assert edge_post(client, "/commands/claim").json() == claimed
    body = observation() | {"outcome": "succeeded"}
    result = edge_post(client, f"/commands/{command['id']}/result", body)
    assert result.status_code == 200, result.text
    completed = result.json()
    assert completed["status"] == "succeeded"
    assert [r["stage"] for r in completed["receipts"]] == [
        "accepted",
        "dispatched",
        "acknowledged",
        "confirmed",
    ]
    assert edge_post(client, f"/commands/{command['id']}/result", body).json() == completed
    assert (
        edge_post(
            client, f"/commands/{command['id']}/result", observation(2) | {"outcome": "succeeded"}
        ).status_code
        == 409
    )
    assert edge_post(client, "/commands/claim").json() is None
    state = client.get(f"/v1/things/{device['id']}/state").json()
    assert state["properties"]["power"]["value"] is True
    assert state["properties"]["power"]["freshness"] == "fresh"
    assert client.get(f"/v1/things/{device['id']}").json()["reachability"] == "online"
    assert client.get(f"/v1/management/devices/{device['id']}").json()["revision"] == 2
    draft = yaml.safe_load(Path("api/openapi.yaml").read_text())
    for name, value in [("Command", completed), ("ThingState", state)]:
        Draft202012Validator(
            {"$ref": f"#/components/schemas/{name}", "components": draft["components"]}
        ).validate(value)


@pytest.mark.integration
def test_edge_and_caller_credentials_cannot_cross_boundaries(execution):
    client, device, edges = execution
    assert client.post("/v1/edge/commands/claim", json={}).status_code == 401
    assert (
        client.get("/v1/things", headers={"Authorization": f"Bearer {EDGE_TOKEN}"}).status_code
        == 401
    )
    response = invoke(client, device)
    command = response.json()
    assert edge_post(client, "/commands/claim", token=OTHER_EDGE).json() is None
    assert (
        edge_post(
            client,
            f"/commands/{command['id']}/result",
            observation() | {"outcome": "succeeded"},
            token=OTHER_EDGE,
        ).status_code
        == 404
    )
    assert (
        edge_post(
            client, f"/things/{device['id']}/observations", observation(), token=FOREIGN_EDGE
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/v1/commands/{command['id']}", headers={"Authorization": f"Bearer {TOKEN_B}"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/things/{device['id']}/actions/set_power",
            headers={"Authorization": f"Bearer {TOKEN_VIEWER}", "Idempotency-Key": "viewer"},
            json={"input": {"value": True}},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/v1/management/devices/{device['id']}/binding", json={"edge_id": edges[1]["id"]}
        ).status_code
        == 409
    )


@pytest.mark.integration
def test_observation_replay_order_and_freshness(execution):
    client, device, _ = execution
    path = f"/things/{device['id']}/observations"
    body = observation(age=60)
    response = edge_post(client, path, body)
    assert response.status_code == 200, response.text
    assert edge_post(client, path, body).json() == response.json()
    assert edge_post(client, path, observation()).status_code == 409
    assert edge_post(client, path, observation(2, age=-20)).status_code == 422
    assert (
        edge_post(
            client, path, observation(2) | {"values": {"power": 1, "brightness": 0}}
        ).status_code
        == 422
    )
    state = client.get(f"/v1/things/{device['id']}/state").json()
    assert state["properties"]["power"]["freshness"] == "stale"
    assert client.get(f"/v1/things/{device['id']}").json()["reachability"] == "offline"
    assert client.get(f"/v1/things/{device['id']}/state?fresh=true").status_code == 501
    assert edge_post(client, path, observation(3)).status_code == 200
    assert edge_post(client, path, observation(2)).status_code == 409


@pytest.mark.integration
def test_unrelated_or_mismatched_observations_do_not_prove_success(execution):
    client, device, _ = execution
    command = invoke(client, device).json()
    edge_post(client, "/commands/claim")
    assert (
        edge_post(
            client,
            f"/commands/{command['id']}/result",
            observation(age=60) | {"outcome": "succeeded"},
        ).status_code
        == 422
    )
    edge_post(client, f"/things/{device['id']}/observations", observation())
    assert client.get(f"/v1/commands/{command['id']}").json()["status"] == "running"
    result = edge_post(
        client,
        f"/commands/{command['id']}/result",
        observation(2, power=False) | {"outcome": "succeeded"},
    )
    assert result.json()["status"] == "failed"
    assert result.json()["receipts"][-1]["error_code"] == "confirmation_mismatch"


@pytest.mark.integration
def test_expiry_and_late_correlated_evidence(execution):
    client, device, _ = execution
    queued = invoke(client, device).json()
    with Session(client.app.state.engine) as session:
        session.get(Command, queued["id"]).deadline = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert edge_post(client, "/commands/claim").json() is None
    assert client.get(f"/v1/commands/{queued['id']}").json()["status"] == "expired"
    active = invoke(client, device, key="next").json()
    edge_post(client, "/commands/claim")
    with Session(client.app.state.engine) as session:
        session.get(Command, active["id"]).deadline = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert client.get(f"/v1/commands/{active['id']}").json()["receipts"][-1]["stage"] == "timed_out"
    result = edge_post(
        client, f"/commands/{active['id']}/result", observation() | {"outcome": "succeeded"}
    ).json()
    assert result["status"] == "succeeded"
    assert "timed_out" in [r["stage"] for r in result["receipts"]]


@pytest.mark.integration
def test_concurrent_intent_is_single_command_and_bad_requests_fail_closed(execution):
    client, device, _ = execution
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: invoke(client, device), range(4)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert invoke(client, device, value="true", key="invalid").status_code == 422
    assert (
        invoke(
            client,
            device,
            key="bad-deadline",
            deadline=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        ).status_code
        == 422
    )
    assert invoke(client, device, key="forged", context={"subject_ref": "admin"}).status_code == 422
    assert (
        client.post(
            f"/v1/things/{device['id']}/actions/unlock",
            headers={"Idempotency-Key": "unlock"},
            json={"input": {}},
        ).status_code
        == 422
    )


@pytest.mark.integration
def test_high_risk_models_and_unassigned_results_are_rejected(execution):
    client, device, edges = execution
    high_risk = virtual_model()
    high_risk["actions"]["set_power"]["risk"] = "high"
    model = publish(client, **(high_risk | {"key": "unsafe_light"}))
    other = register(client, model, external_ref="virtual:unsafe").json()
    assert (
        client.put(
            f"/v1/management/devices/{other['id']}/binding", json={"edge_id": edges[0]["id"]}
        ).status_code
        == 422
    )
    command = invoke(client, device).json()
    assert (
        edge_post(
            client, f"/commands/{command['id']}/result", observation() | {"outcome": "succeeded"}
        ).status_code
        == 409
    )
    edge_post(client, "/commands/claim")
    report = {"outcome": "failed", "error_code": "adapter_failure"}
    response = edge_post(client, f"/commands/{command['id']}/result", report)
    assert response.json()["status"] == "failed"
    assert response.json()["receipts"][-1]["error_code"] == "adapter_failure"


@pytest.mark.integration
def test_runtime_refuses_lossy_domain_rollback(execution):
    client, device, _ = execution
    original = client.get(f"/v1/management/devices/{device['id']}").json()
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade(Config("alembic.ini"), "0002_registry")
    registered = client.get(f"/v1/management/devices/{device['id']}").json()
    assert registered["lifecycle_status"] == original["lifecycle_status"]
    assert registered["model_id"] == device["model_id"]
    edge = edge_post(client, "/register", {"title": "Recreated test Edge"}).json()
    assert (
        client.put(
            f"/v1/management/devices/{device['id']}/binding", json={"edge_id": edge["id"]}
        ).status_code
        == 200
    )


@pytest.mark.integration
def test_legacy_node_adoption_preserves_binding_and_identity(execution):
    client, device, edges = execution
    response = client.post(
        "/v1/management/nodes", json={"edge_ref": edges[0]["edge_ref"], "title": "Adopted Node"}
    )
    assert response.status_code == 201
    assert response.json()["id"] == edges[0]["id"]
    assert client.get(f"/v1/things/{device['id']}/binding").json()["edge_id"] == edges[0]["id"]
    assert (
        client.post(
            "/v1/management/nodes",
            json={"edge_ref": edges[0]["edge_ref"], "title": "Do not rotate implicitly"},
        ).status_code
        == 409
    )


@pytest.mark.integration
def test_worker_restart_outbox_and_deduplication(execution, tmp_path):
    client, device, _ = execution

    def request(method, path, data=None):
        response = client.request(
            method, "/v1" + path, headers={"Authorization": f"Bearer {EDGE_TOKEN}"}, json=data
        )
        assert response.status_code == 200, response.text
        return response.json()

    journal = tmp_path / "journal.sqlite3"
    worker = VirtualEdge(journal, request)
    worker.tick()
    command = invoke(client, device).json()
    claimed = request("POST", "/edge/commands/claim", {})
    worker.execute(claimed)
    worker.close()  # action applied locally, result not yet uploaded
    worker = VirtualEdge(journal, request)
    worker.flush()
    worker.execute(claimed)  # duplicate delivery reuses exact recorded result
    worker.flush()
    worker.close()
    completed = client.get(f"/v1/commands/{command['id']}").json()
    assert completed["status"] == "succeeded"
    assert len(completed["receipts"]) == 4
    with Session(client.app.state.engine) as session:
        assert session.get(StateProjection, device["id"]).values["power"] is True

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from agenticiot.config import Settings
from agenticiot.main import create_app
from agenticiot.registry.models import Device
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from test_registry_schema import light_definition

TOKEN_A = "test-operator-a-" + "a" * 32
TOKEN_B = "test-operator-b-" + "b" * 32
TOKEN_VIEWER = "test-viewer-a-" + "v" * 32


@pytest.fixture
def registry_client(monkeypatch):
    url = os.environ.get("AGENTICIOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a dedicated PostgreSQL database")
    monkeypatch.setenv("AGENTICIOT_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    domain_a, domain_b = "home:a-" + uuid4().hex, "home:b-" + uuid4().hex
    settings = Settings(
        _env_file=None,
        api_clients=[
            {
                "token": TOKEN_A,
                "subject_ref": "operator:a",
                "domain_ref": domain_a,
                "role": "operator",
            },
            {
                "token": TOKEN_B,
                "subject_ref": "operator:b",
                "domain_ref": domain_b,
                "role": "operator",
            },
            {
                "token": TOKEN_VIEWER,
                "subject_ref": "viewer:a",
                "domain_ref": domain_a,
                "role": "viewer",
            },
        ],
    )
    with TestClient(create_app(settings, legacy_test_channel=True)) as client:
        from agenticiot.access.service import bootstrap_configured

        bootstrap_configured(client.app.state.engine, settings)
        client.headers["Authorization"] = f"Bearer {TOKEN_A}"
        yield client, domain_a, domain_b


def publish(client, **overrides):
    data = light_definition() | overrides
    response = client.post("/v1/management/models", json=data)
    assert response.status_code == 201, response.text
    return response.json()


def register(client, model, external_ref="virtual:light", **overrides):
    response = client.post(
        "/v1/management/devices",
        json={
            "model_id": model["id"],
            "title": "Living room light",
            "external_ref": external_ref,
            "space_ref": "room:living",
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    return response


@pytest.mark.integration
def test_model_version_registration_discovery_and_atomic_audit(registry_client):
    client, domain, _ = registry_client
    old_model = publish(client)
    assert client.post("/v1/management/models", json=light_definition()).status_code == 409
    assert (
        client.patch(
            f"/v1/management/models/{old_model['id']}", json={"title": "overwrite"}
        ).status_code
        == 405
    )
    new_model = publish(client, version="1.1.0")
    assert new_model["id"] != old_model["id"]
    response = register(client, old_model)
    device = response.json()
    assert response.headers["etag"] == '"1"'
    assert device["model_version"] == "1.0.0"
    assert device["domain_ref"] == client.get("/v1/management/domain").json()["id"]
    assert device["domain_ref"] != domain
    assert device["lifecycle_status"] == "commissioning"
    assert device["reachability"] == "unknown"
    assert client.get(response.headers["location"]).status_code == 200
    duplicate = client.post(
        "/v1/management/devices",
        json={"model_id": old_model["id"], "title": "Duplicate", "external_ref": "virtual:light"},
    )
    assert duplicate.status_code == 409
    thing = client.get(f"/v1/things/{device['id']}").json()
    draft = yaml.safe_load(Path("api/openapi.yaml").read_text())
    Draft202012Validator(
        {"$ref": "#/components/schemas/Thing", "components": draft["components"]}
    ).validate(thing)
    assert thing["actions"]["set_power"]["input_schema"]["required"] == ["value"]
    assert (
        client.get("/v1/things?capability=set_power&space_ref=room:living").json()["items"][0]["id"]
        == device["id"]
    )
    assert client.get("/v1/things?capability=unlock").json()["items"] == []
    audit = client.get("/v1/management/audit").json()["items"]
    assert len(audit) == 3  # two successful publications + one successful registration
    assert {event["subject_ref"] for event in audit} == {"operator:a"}
    assert all(event["trace_id"] for event in audit)
    assert TOKEN_A not in str(audit)


@pytest.mark.integration
def test_registry_survives_application_restart(registry_client):
    client, domain, _ = registry_client
    model = publish(client)
    device = register(client, model).json()
    # A fresh app/engine reads the same persisted records, without in-memory fixtures.
    with TestClient(create_app(client.app.state.settings)) as restarted:
        restarted.headers["Authorization"] = f"Bearer {TOKEN_A}"
        response = restarted.get(f"/v1/things/{device['id']}")
        assert response.status_code == 200
        assert response.json()["domain_ref"] == client.get("/v1/management/domain").json()["id"]
        assert response.json()["model_version"] == "1.0.0"
        assert len(restarted.get("/v1/management/audit").json()["items"]) == 2


@pytest.mark.integration
def test_domain_isolation_and_viewer_permissions(registry_client):
    client, domain_a, domain_b = registry_client
    model = publish(client)
    device = register(client, model).json()
    foreign = {"Authorization": f"Bearer {TOKEN_B}"}
    assert client.get("/v1/things", headers=foreign).json()["items"] == []
    for path in [
        f"/v1/things/{device['id']}",
        f"/v1/management/devices/{device['id']}",
        f"/v1/management/models/{model['id']}",
    ]:
        assert client.get(path, headers=foreign).status_code == 404
    assert client.get(f"/v1/things?domain_ref={domain_a}", headers=foreign).status_code == 403
    assert (
        client.post(
            "/v1/management/devices",
            headers=foreign,
            json={"model_id": model["id"], "title": "bad", "external_ref": "bad"},
        ).status_code
        == 404
    )
    forged = light_definition() | {"domain_ref": domain_b}
    assert client.post("/v1/management/models", json=forged).status_code == 422
    assert client.get("/v1/management/audit", headers=foreign).json()["items"] == []
    viewer = {"Authorization": f"Bearer {TOKEN_VIEWER}"}
    assert client.get("/v1/things", headers=viewer).status_code == 200
    assert (
        client.post("/v1/management/models", headers=viewer, json=light_definition()).status_code
        == 403
    )
    assert (
        client.patch(
            f"/v1/management/devices/{device['id']}", headers=viewer, json={"title": "bad"}
        ).status_code
        == 403
    )
    assert client.get("/v1/management/audit", headers=viewer).status_code == 403


@pytest.mark.integration
def test_conditional_edit_and_stale_write(registry_client):
    client, _, _ = registry_client
    device = register(client, publish(client)).json()
    path = f"/v1/management/devices/{device['id']}"
    assert client.patch(path, json={"title": "New"}).status_code == 428
    response = client.patch(
        path, headers={"If-Match": '"1"'}, json={"title": "New", "space_ref": None}
    )
    assert response.status_code == 200
    assert response.headers["etag"] == '"2"'
    assert response.json()["space_ref"] is None
    stale = client.patch(path, headers={"If-Match": '"1"'}, json={"title": "Stale"})
    assert stale.status_code == 412
    assert client.get(path).json()["title"] == "New"
    assert len(client.get("/v1/management/audit").json()["items"]) == 3
    # Verify the database version guard itself, not only the API precondition check.
    with Session(client.app.state.engine) as first, Session(client.app.state.engine) as second:
        a = first.scalar(select(Device).where(Device.id == device["id"]))
        b = second.scalar(select(Device).where(Device.id == device["id"]))
        a.title = "Concurrent winner"
        first.commit()
        b.title = "Concurrent loser"
        with pytest.raises(StaleDataError):
            second.commit()


@pytest.mark.integration
def test_cursor_is_stable_scoped_and_validated(registry_client):
    client, _, _ = registry_client
    for version in ["1.0.0", "1.1.0", "1.2.0"]:
        publish(client, version=version)
    first = client.get("/v1/management/models?limit=2").json()
    second = client.get(
        "/v1/management/models", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert len({item["id"] for item in first["items"] + second["items"]}) == 3
    assert second["next_cursor"] is None
    assert (
        client.get("/v1/management/devices", params={"cursor": first["next_cursor"]}).status_code
        == 400
    )
    assert (
        client.get(
            "/v1/management/models",
            params={"cursor": first["next_cursor"]},
            headers={"Authorization": f"Bearer {TOKEN_B}"},
        ).status_code
        == 400
    )
    for invalid in ["garbage", "e30=", "W10=", "bnVsbA=="]:
        assert client.get("/v1/management/models", params={"cursor": invalid}).status_code == 400


@pytest.mark.integration
def test_concurrent_duplicate_registration_produces_one_device(registry_client):
    client, _, _ = registry_client
    model = publish(client)
    payload = {"model_id": model["id"], "external_ref": "shared-endpoint", "title": "Shared"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: client.post("/v1/management/devices", json=deepcopy(payload)).status_code,
                range(2),
            )
        )
    assert sorted(results) == [201, 409]
    assert len(client.get("/v1/management/devices").json()["items"]) == 1
    assert len(client.get("/v1/management/audit").json()["items"]) == 2


def test_registry_is_denied_without_configured_credentials():
    with TestClient(
        create_app(Settings(_env_file=None, api_clients=[]), readiness_probe=lambda: True)
    ) as client:
        for path in [
            "/v1/things",
            "/v1/management/models",
            "/v1/management/devices",
            "/v1/management/audit",
        ]:
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
        assert client.get("/health/live").status_code == 200

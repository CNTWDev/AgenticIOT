# ruff: noqa: F811
"""Regression tests for durable recovery, dispatch credits and transport bounds."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from agenticiot.limits import RequestLimits
from agenticiot.node import NodeRuntime
from agenticiot.nodes.channel import Connection, acquire, transaction
from agenticiot.runtime.models import Command, Observation
from agenticiot.runtime.schemas import EdgeRegistration
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_access_nodes_services import provisioned
from test_registry import TOKEN_B, TOKEN_VIEWER, publish, register, registry_client  # noqa: F401
from test_runtime import virtual_model


def test_node_tombstone_survives_restart_and_prevents_late_execution(tmp_path):
    async def run():
        node = NodeRuntime(tmp_path)
        await node.start()
        command_id = uuid4().hex
        await node.journal(node.receive_command, command_id)
        await node.journal(node.reconcile, command_id)
        await node.stop()
        node = NodeRuntime(tmp_path)
        await node.start()
        try:
            assert not await node.journal(node.prepare, {"id": command_id})
            rows = await node.journal(
                lambda: node.worker.db.execute("SELECT payload FROM outbox").fetchall()
            )
            assert "not_executed" in rows[0][0]
        finally:
            await node.stop()

    asyncio.run(run())


def test_handshake_rejections_retry_without_logging_token(tmp_path, monkeypatch, caplog):
    from websockets.exceptions import InvalidHandshake

    async def run():
        node = NodeRuntime(tmp_path)
        attempts = []

        async def rejected(*_):
            attempts.append(1)
            if len(attempts) == 3:
                raise asyncio.CancelledError
            raise InvalidHandshake("secret-token")

        async def no_wait(_):
            return

        monkeypatch.setattr(node, "run_connection", rejected)
        monkeypatch.setattr(asyncio, "sleep", no_wait)
        with pytest.raises(asyncio.CancelledError):
            await node.run_forever("wss://example.invalid", "secret-token")
        assert len(attempts) == 3
        node.executor.shutdown()

    asyncio.run(run())
    assert "secret-token" not in caplog.text


def test_capacity_buckets_do_not_starve_liveness_or_nodes():
    async def run():
        called = []

        async def app(scope, receive, send):
            called.append(scope["path"])

        limits = RequestLimits(app)
        limits.active["http"] = 64
        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        await limits({"type": "http", "method": "GET", "path": "/v1/things"}, receive, send)
        assert messages[0]["status"] == 503
        await limits({"type": "http", "method": "GET", "path": "/health/live"}, receive, send)
        await limits({"type": "websocket", "path": "/v1/nodes/channel"}, receive, send)
        assert called == ["/health/live", "/v1/nodes/channel"]
        assert limits.active["nodes"] == 0

    asyncio.run(run())


def test_slow_upload_times_out_and_releases_admission(monkeypatch):
    import agenticiot.limits as module

    monkeypatch.setattr(module, "BODY_TIMEOUT", 0.01)

    async def run():
        async def app(*_):
            raise AssertionError("Incomplete body must not reach application")

        async def receive():
            await asyncio.sleep(10)

        messages = []

        async def send(message):
            messages.append(message)

        limits = RequestLimits(app)
        await limits(
            {"type": "http", "path": "/v1/chat/completions", "method": "POST"}, receive, send
        )
        assert messages[0]["status"] == 408
        assert limits.active["http"] == 0

    asyncio.run(run())


def test_deployment_configuration_check_is_secret_safe(monkeypatch, capsys):
    from types import SimpleNamespace

    import agenticiot.deploy_check as check

    monkeypatch.setattr(
        check, "Settings", lambda: SimpleNamespace(api_clients=[], trusted_issuers=[])
    )
    with pytest.raises(SystemExit):
        check.main()
    assert "Invalid deployment configuration" in capsys.readouterr().err
    monkeypatch.setattr(
        check, "Settings", lambda: SimpleNamespace(api_clients=[object()], trusted_issuers=[])
    )
    check.main()
    assert "validated" in capsys.readouterr().out


@pytest.mark.integration
@pytest.mark.parametrize("offset", [60, -60])
def test_dispatch_credits_clock_skew_and_reconciliation(registry_client, offset):
    client, _, _ = registry_client
    node = provisioned(client)
    principal, node_id, epoch = acquire(client.app, node["token"], EdgeRegistration(title="Home"))
    connection = Connection(node_id, epoch, capacity=2, clock_offset=offset)
    model = publish(client, **virtual_model())
    commands = []
    for i in range(3):
        device = register(client, model, external_ref=str(i)).json()
        assert (
            client.put(
                f"/v1/management/devices/{device['id']}/binding", json={"edge_id": node_id}
            ).status_code
            == 200
        )
        commands.append(
            client.post(
                f"/v1/things/{device['id']}/actions/set_power",
                json={"input": {"value": True}},
                headers={"Idempotency-Key": str(i)},
            ).json()
        )
    sent = set()
    selected = transaction(client.app, principal, connection, "dispatch", sent)
    assert len(selected) == 2
    sent.update(c["id"] for c in selected)
    assert transaction(client.app, principal, connection, "dispatch", sent) == []
    first = selected[0]
    transaction(client.app, principal, connection, "received", {"command_id": first["id"]})
    raw_time = datetime.now(UTC) - timedelta(seconds=offset)
    transaction(
        client.app,
        principal,
        connection,
        "result",
        {
            "command_id": first["id"],
            "data": {
                "outcome": "succeeded",
                "source_sequence": 1,
                "observed_at": raw_time.isoformat(),
                "values": {"power": True, "brightness": 0},
            },
        },
    )
    with Session(client.app.state.engine) as session:
        observed = session.scalar(select(Observation).where(Observation.command_id == first["id"]))
        assert observed.source_observed_at == raw_time
        assert (observed.observed_at - raw_time).total_seconds() == offset
    second = selected[1]
    with Session(client.app.state.engine) as session, session.begin():
        session.get(Command, second["id"]).deadline = datetime.now(UTC) - timedelta(seconds=1)
    result = client.get(f"/v1/commands/{second['id']}").json()
    assert result["blocked"] and result["status"] == "unknown"
    # Absence of received acknowledgement did not claim nonexecution.
    assert result["received_at"] is None
    transaction(
        client.app,
        principal,
        connection,
        "reconciled",
        {"command_id": second["id"], "data": {"outcome": "uncertain"}},
    )
    path = f"/v1/commands/{second['id']}/reconcile"
    body = {"observation_id": observed.id, "reason": "Operator inspected current state"}
    assert client.post(path, json=body).status_code == 409
    assert (
        client.post(
            path, json=body, headers={"Authorization": f"Bearer {TOKEN_VIEWER}"}
        ).status_code
        == 403
    )
    assert (
        client.post(path, json=body, headers={"Authorization": f"Bearer {TOKEN_B}"}).status_code
        == 404
    )
    transaction(
        client.app,
        principal,
        connection,
        "observation",
        {
            "thing_id": second["thing_id"],
            "data": {
                "source_sequence": 2,
                "observed_at": (datetime.now(UTC) - timedelta(seconds=offset)).isoformat(),
                "values": {"power": False, "brightness": 0},
            },
        },
    )
    body["observation_id"] = client.get(f"/v1/things/{second['thing_id']}/state").json()[
        "observation_id"
    ]
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["blocked"] is False and response.json()["status"] == "unknown"
    third = commands[2]
    transaction(client.app, principal, connection, "dispatch", set())
    with Session(client.app.state.engine) as session, session.begin():
        session.get(Command, third["id"]).deadline = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get(f"/v1/commands/{third['id']}").json()["blocked"] is True
    transaction(
        client.app,
        principal,
        connection,
        "reconciled",
        {
            "command_id": third["id"],
            "data": {"outcome": "not_executed"},
        },
    )
    result = client.get(f"/v1/commands/{third['id']}").json()
    assert result["status"] == "failed" and result["blocked"] is False
    # Maintenance retains command and manual-reconciliation evidence, even after aging.
    from agenticiot.services.api import expire_metadata

    with Session(client.app.state.engine) as session, session.begin():
        rows = session.scalars(select(Observation).where(Observation.edge_id == node_id)).all()
        for row in rows:
            row.received_at = datetime.now(UTC) - timedelta(days=31)
    expire_metadata(client.app)
    with Session(client.app.state.engine) as session:
        assert session.get(Observation, observed.id) is not None
        assert session.get(Observation, body["observation_id"]) is not None


@pytest.mark.integration
def test_turn_context_scopes_stable_operation_keys(registry_client):
    from dataclasses import replace

    from agenticiot.runtime.schemas import InvokeRequest
    from agenticiot.runtime.service import RuntimeService
    from agenticiot.security import principal_from_token
    from test_registry import TOKEN_A

    client, _, _ = registry_client
    node = provisioned(client)
    device = register(client, publish(client, **virtual_model())).json()
    assert (
        client.put(
            f"/v1/management/devices/{device['id']}/binding", json={"edge_id": node["id"]}
        ).status_code
        == 200
    )
    principal = principal_from_token(client.app, TOKEN_A)
    ids = []
    for turn in ("turn-one", "turn-one", "turn-two"):
        with Session(client.app.state.engine, expire_on_commit=False) as session:
            service = RuntimeService(session, replace(principal, turn_ref=turn), uuid4().hex)
            result = service.invoke(
                device["id"], "set_power", InvokeRequest(input={"value": True}), "same-operation"
            )
            ids.append(result.id)
    assert ids[0] == ids[1] and ids[1] != ids[2]


@pytest.mark.integration
def test_last_manager_and_no_redirect(registry_client):
    client, _, _ = registry_client
    grant = next(
        g
        for g in client.get("/v1/management/grants").json()["items"]
        if "domain:manage" in g["permissions"]
    )
    assert client.post(f"/v1/management/grants/{grant['id']}/revoke").status_code == 409
    response = client.post("/v1/chat/completions/", json={}, follow_redirects=False)
    assert response.status_code == 404 and "location" not in response.headers

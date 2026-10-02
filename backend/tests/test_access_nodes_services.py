# ruff: noqa: F811
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from agenticiot.access.models import DomainAlias, DomainGrant
from agenticiot.access.service import MANAGE, provision
from agenticiot.config import TrustedIssuer
from agenticiot.nodes.channel import Connection, acquire, transaction
from agenticiot.runtime.schemas import EdgeRegistration
from agenticiot.services.models import Invocation
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_registry import TOKEN_B, TOKEN_VIEWER, registry_client  # noqa: F401


def provisioned(client):
    response = client.post("/v1/management/nodes", json={"edge_ref": "home:node", "title": "Home"})
    assert response.status_code == 201, response.text
    return response.json()


def service(client, node, enabled=True):
    response = client.post(
        "/v1/management/service-connections",
        json={"node_id": node["id"], "name": "home-model", "local_ref": "ollama", "model": "tiny"},
    )
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["enabled"] is False and row["locality"] == "declared_local"
    if enabled:
        assert (
            client.patch(
                f"/v1/management/service-connections/{row['id']}/activation", json={"enabled": True}
            ).status_code
            == 200
        )
    return row


def hello(ws):
    ws.send_json({"type": "hello", "version": 1, "registration": {"title": "Home"}})
    message = ws.receive_json()
    assert message["type"] == "welcome"
    return message


def receive(ws, kind):
    for _ in range(20):
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"No {kind} frame")


@pytest.mark.integration
def test_signed_identity_permissions_are_intersection_and_revocable(registry_client):
    client, _, _ = registry_client
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    issuer = TrustedIssuer(
        issuer="test:entry:" + uuid4().hex, client_id="app", key_id="k1", public_key=public.decode()
    )
    client.app.state.settings.trusted_issuers = [issuer]
    with Session(client.app.state.engine) as session, session.begin():
        domain_id = provision(
            session, issuer.issuer, issuer.client_id, "family", "person", {"device:read"}
        )
    now = datetime.now(UTC)
    claims = {
        "iss": issuer.issuer,
        "aud": "agenticiot-platform",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "sub": "person",
        "client_id": "app",
        "domain_ref": "family",
        "permissions": sorted(MANAGE),
    }

    def auth(changes=None):
        token = jwt.encode(
            claims | (changes or {}), private, algorithm="EdDSA", headers={"kid": "k1"}
        )
        return {"Authorization": f"Bearer {token}"}

    result = client.get("/v1/management/domain", headers=auth())
    assert result.json()["id"] == domain_id
    assert result.json()["permissions"] == ["device:read"]
    assert (
        client.post(
            "/v1/management/nodes", json={"edge_ref": "x", "title": "X"}, headers=auth()
        ).status_code
        == 403
    )
    assert (
        client.get("/v1/management/domain", headers=auth({"domain_ref": "other"})).status_code
        == 403
    )
    assert client.get("/v1/management/domain", headers=auth({"aud": "wrong"})).status_code == 401
    assert (
        client.get(
            "/v1/management/domain", headers=auth({"exp": now - timedelta(seconds=1)})
        ).status_code
        == 401
    )
    with Session(client.app.state.engine) as session, session.begin():
        alias = session.scalar(select(DomainAlias).where(DomainAlias.domain_id == domain_id))
        grant = session.scalar(select(DomainGrant).where(DomainGrant.alias_id == alias.id))
        grant.enabled = False
    assert client.get("/v1/management/domain", headers=auth()).status_code == 403


@pytest.mark.integration
def test_node_identity_activation_and_session_fencing(registry_client):
    client, _, _ = registry_client
    node = provisioned(client)
    assert client.get("/v1/management/nodes").json()["items"][0]["online"] is False
    assert (
        client.get(
            "/v1/management/domain", headers={"Authorization": f"Bearer {node['token']}"}
        ).status_code
        == 401
    )
    assert (
        client.patch(
            f"/v1/management/nodes/{node['id']}/activation",
            json={"enabled": False},
            headers={"Authorization": f"Bearer {TOKEN_B}"},
        ).status_code
        == 404
    )
    principal, node_id, epoch = acquire(client.app, node["token"], EdgeRegistration(title="Home"))
    acquire(client.app, node["token"], EdgeRegistration(title="Home"))
    from agenticiot.security import APIError

    with pytest.raises(APIError) as error:
        transaction(client.app, principal, Connection(node_id, epoch), "heartbeat")
    assert error.value.code == "stale_session"
    with client.websocket_connect(
        "/v1/nodes/channel", headers={"Authorization": f"Bearer {node['token']}"}
    ) as ws:
        assert hello(ws)["node_id"] == node_id
        assert receive(ws, "bindings")["items"] == []
    client.patch(f"/v1/management/nodes/{node_id}/activation", json={"enabled": False})
    with pytest.raises(Exception) as error:
        with client.websocket_connect(
            "/v1/nodes/channel", headers={"Authorization": f"Bearer {node['token']}"}
        ):
            pass
    assert type(error.value).__name__ in {"WebSocketDisconnect", "WebSocketDenialResponse"}
    rotated = client.post(f"/v1/management/nodes/{node_id}/rotate-credential").json()
    assert rotated["token"] != node["token"]
    assert client.get("/v1/management/nodes").json()["items"][0]["enabled"] is False


@pytest.mark.integration
def test_local_stream_metadata_privacy_and_optional_dedup(registry_client):
    client, _, _ = registry_client
    client.app.state.settings.inference_hmac_key = SecretStr("h" * 32)
    node = provisioned(client)
    row = service(client, node, False)
    body = {
        "model": "home-model",
        "messages": [{"role": "user", "content": "private-prompt"}],
        "stream": True,
    }
    assert client.post("/v1/chat/completions", json=body).status_code == 409
    client.patch(
        f"/v1/management/service-connections/{row['id']}/activation", json={"enabled": True}
    )
    assert (
        client.post(
            "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {TOKEN_VIEWER}"}
        ).status_code
        == 403
    )
    assert client.post("/v1/chat/completions", json=body | {"tools": []}).status_code == 422
    with client.websocket_connect(
        "/v1/nodes/channel", headers={"Authorization": f"Bearer {node['token']}"}
    ) as ws:
        hello(ws)
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(
                client.post,
                "/v1/chat/completions",
                json=body,
                headers={"Idempotency-Key": "intent-1"},
            )
            call = receive(ws, "inference")
            assert call["request"]["model"] == "tiny"
            ws.send_json(
                {
                    "type": "delta",
                    "invocation_id": call["invocation_id"],
                    "content": "private-output",
                }
            )
            ws.send_json(
                {
                    "type": "complete",
                    "invocation_id": call["invocation_id"],
                    "finish_reason": "stop",
                }
            )
            response = pending.result(timeout=10)
    assert response.status_code == 200 and "data: [DONE]" in response.text
    call_id = response.headers["X-Invocation-ID"]
    assert client.get(f"/v1/invocations/{call_id}").json()["state"] == "succeeded"
    duplicate = client.post(
        "/v1/chat/completions", json=body, headers={"Idempotency-Key": "intent-1"}
    )
    assert duplicate.status_code == 409 and duplicate.headers["X-Invocation-ID"] == call_id
    client.app.state.settings.inference_hmac_previous_keys = [SecretStr("h" * 32)]
    client.app.state.settings.inference_hmac_key = SecretStr("new-key-" * 8)
    assert (
        client.post(
            "/v1/chat/completions", json=body, headers={"Idempotency-Key": "intent-1"}
        ).headers["X-Invocation-ID"]
        == call_id
    )
    changed = body | {"messages": [{"role": "user", "content": "different"}]}
    assert (
        client.post(
            "/v1/chat/completions", json=changed, headers={"Idempotency-Key": "intent-1"}
        ).json()["code"]
        == "idempotency_conflict"
    )
    with Session(client.app.state.engine) as session:
        stored = session.get(Invocation, call_id)
        values = {
            column.name: getattr(stored, column.name) for column in Invocation.__table__.columns
        }
        assert "private-prompt" not in str(values) and "private-output" not in str(values)
    assert (
        client.get(
            f"/v1/invocations/{call_id}", headers={"Authorization": f"Bearer {TOKEN_B}"}
        ).status_code
        == 404
    )


def test_control_messages_do_not_share_inference_queue():
    async def check():
        connection = Connection(uuid4().hex, 1)
        for _ in range(32):
            await connection.send({"type": "inference"}, data=True)
        await connection.send({"type": "command"})
        assert connection.ready.is_set()
        assert connection.control.get_nowait()["type"] == "command"

    asyncio.run(check())


@pytest.mark.integration
def test_grant_revocation_cannot_be_undone_by_bootstrap(registry_client):
    from agenticiot.access.service import bootstrap_configured

    client, _, _ = registry_client
    grant = next(
        g
        for g in client.get("/v1/management/grants").json()["items"]
        if g["subject_ref"] == "viewer:a"
    )
    assert (
        client.post(
            f"/v1/management/grants/{grant['id']}/revoke",
            headers={"Authorization": f"Bearer {TOKEN_B}"},
        ).status_code
        == 404
    )
    assert client.post(f"/v1/management/grants/{grant['id']}/revoke").status_code == 200
    bootstrap_configured(client.app.state.engine, client.app.state.settings)
    assert (
        client.get("/v1/things", headers={"Authorization": f"Bearer {TOKEN_VIEWER}"}).status_code
        == 403
    )


@pytest.mark.integration
def test_unknown_stays_terminal_late_facts_and_request_bounds(registry_client):
    from agenticiot.security import principal_from_token
    from agenticiot.services.api import ChatRequest, admit, expire_metadata, finish, late_fact
    from test_registry import TOKEN_A

    client, _, _ = registry_client
    node = provisioned(client)
    service(client, node)
    principal = principal_from_token(client.app, TOKEN_A)
    body = ChatRequest(model="home-model", messages=[{"role": "user", "content": "test"}])
    client.app.state.node_hub.connections[node["id"]] = Connection(node["id"], 1)
    _, call_id, _ = admit(client.app, principal, body, None)
    client.app.state.node_hub.connections.clear()
    with Session(client.app.state.engine) as session, session.begin():
        session.get(Invocation, call_id).deadline = datetime.now(UTC) - timedelta(seconds=6)
    expire_metadata(client.app)
    finish(client.app, call_id, "succeeded")
    late_fact(client.app, uuid4().hex, {"invocation_id": call_id})
    assert client.get(f"/v1/invocations/{call_id}").json()["late_report"] is None
    late_fact(client.app, node["id"], {"invocation_id": call_id})
    result = client.get(f"/v1/invocations/{call_id}").json()
    assert result["state"] == "unknown" and result["late_report"]["node_id"] == node["id"]
    response = client.post("/v1/chat/completions", content=" " * 131073)
    assert response.status_code == 413
    tool = client.get("/v1/tools").json()["tools"][3]
    assert "operation_id" not in tool["input_schema"]["properties"]
    assert tool["runtime_injected_fields"] == ["operation_id"]


@pytest.mark.integration
def test_service_revocation_terminates_stream_without_success(registry_client):
    client, _, _ = registry_client
    node = provisioned(client)
    row = service(client, node)
    with client.websocket_connect(
        "/v1/nodes/channel", headers={"Authorization": f"Bearer {node['token']}"}
    ) as ws:
        hello(ws)
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(
                client.post,
                "/v1/chat/completions",
                json={"model": "home-model", "messages": [{"role": "user", "content": "test"}]},
            )
            call = receive(ws, "inference")
            client.patch(
                f"/v1/management/service-connections/{row['id']}/activation",
                json={"enabled": False},
            )
            response = pending.result(timeout=5)
    assert "execution_unknown" in response.text and "[DONE]" not in response.text
    assert client.get(f"/v1/invocations/{call['invocation_id']}").json()["state"] == "unknown"

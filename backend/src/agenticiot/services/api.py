import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import uuid4

import anyio
from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import Field, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agenticiot.access.api import Database, Enabled
from agenticiot.nodes.models import NodeCredential
from agenticiot.registry.schemas import ResourceID, SchemaModel
from agenticiot.registry.service import RegistryService
from agenticiot.runtime.models import EdgeNode
from agenticiot.security import APIError, Authenticated, Operator, revalidate
from agenticiot.services.models import Invocation, ServiceConnection

router = APIRouter(prefix="/v1", tags=["Node local services"])


class ServiceView(SchemaModel):
    id: ResourceID
    name: str
    node_id: ResourceID
    local_ref: str
    model: str
    enabled: bool
    declared_by: str
    locality: Literal["declared_local"]
    node_connected: bool
    service_health: Literal["unknown"]
    created_at: datetime


class ServiceList(SchemaModel):
    items: list[ServiceView]


class InvocationView(SchemaModel):
    id: ResourceID
    state: Literal["admitted", "running", "succeeded", "failed", "unknown"]
    service_id: ResourceID
    created_at: datetime
    completed_at: datetime | None
    usage: dict[str, int] | None
    late_report: dict | None
    result_replay: Literal[False]


class ServiceCreate(SchemaModel):
    node_id: ResourceID
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    local_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    model: str = Field(min_length=1, max_length=200)


class Message(SchemaModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=32768)


class ChatRequest(SchemaModel):
    model: str = Field(min_length=1, max_length=64, description="Authorized service name, not URL")
    messages: list[Message] = Field(min_length=1, max_length=100)
    stream: Literal[True] = True
    n: Literal[1] = 1
    max_tokens: int = Field(default=512, ge=1, le=4096, strict=True)
    temperature: float = Field(default=0.7, ge=0, le=2, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json().encode()) > 65536:
            raise ValueError("Text request exceeds 64 KiB")
        return self


def view(row, app):
    connection = app.state.node_hub.connections.get(row.node_id)
    return {
        "id": row.id,
        "name": row.name,
        "node_id": row.node_id,
        "local_ref": row.local_ref,
        "model": row.model,
        "enabled": row.enabled,
        "declared_by": row.declared_by,
        "locality": "declared_local",
        "node_connected": bool(connection and not connection.closed),
        "service_health": "unknown",
        "created_at": row.created_at.isoformat(),
    }


@router.post(
    "/management/service-connections",
    status_code=201,
    operation_id="registerLocalService",
    response_model=ServiceView,
)
def register(data: ServiceCreate, request: Request, principal: Operator, session: Database):
    node = session.scalar(
        select(EdgeNode).where(
            EdgeNode.id == data.node_id, EdgeNode.domain_ref == principal.domain_id
        )
    )
    if node is None:
        raise APIError(404, "resource_not_found", "Node not found")
    row = ServiceConnection(
        id=uuid4().hex,
        domain_id=principal.domain_id,
        declared_by=principal.subject_ref,
        **data.model_dump(),
    )
    session.add(row)
    RegistryService(session, principal, request.state.trace_id).audit("service.registered", row.id)
    session.commit()
    return view(row, request.app)


@router.patch(
    "/management/service-connections/{service_id}/activation",
    operation_id="setLocalServiceActivation",
    response_model=ServiceView,
)
def activation(
    service_id: str, data: Enabled, request: Request, principal: Operator, session: Database
):
    row = session.scalar(
        select(ServiceConnection)
        .where(
            ServiceConnection.id == service_id, ServiceConnection.domain_id == principal.domain_id
        )
        .with_for_update()
    )
    if row is None:
        raise APIError(404, "resource_not_found", "Service not found")
    row.enabled = data.enabled
    RegistryService(session, principal, request.state.trace_id).audit("service.activation", row.id)
    session.commit()
    return view(row, request.app)


@router.get("/services", operation_id="listLocalServices", response_model=ServiceList)
def services(request: Request, principal: Authenticated, session: Database):
    principal.require("service:invoke")
    rows = session.scalars(
        select(ServiceConnection)
        .where(ServiceConnection.domain_id == principal.domain_id)
        .order_by(ServiceConnection.id)
        .limit(200)
    )
    return {"items": [view(row, request.app) for row in rows]}


def scope_for(principal):
    return hashlib.sha256(
        json.dumps(
            [principal.issuer, principal.client_id, principal.domain_id, principal.subject_ref]
        ).encode()
    ).hexdigest()


def admit(app, principal, body, key):
    principal.require("service:invoke")
    scope = scope_for(principal)
    digest = key_hash = key_version = None
    candidates = {}
    if key is not None:
        secret = app.state.settings.inference_hmac_key
        if secret is None:
            raise APIError(503, "idempotency_unconfigured", "Configure the inference HMAC key")

        for signing_key in [secret, *app.state.settings.inference_hmac_previous_keys]:

            def mac(value, secret=signing_key):
                return hmac.new(
                    secret.get_secret_value().encode(), value.encode(), hashlib.sha256
                ).hexdigest()

            hashed = mac(scope + ":" + key)
            fingerprint = mac(json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":")))
            candidates[hashed] = fingerprint
            if key_hash is None:
                key_hash, digest = hashed, fingerprint
                key_version = hashlib.sha256(signing_key.get_secret_value().encode()).hexdigest()[
                    :16
                ]
    with Session(app.state.engine, expire_on_commit=False) as session:
        row = session.scalar(
            select(ServiceConnection)
            .where(
                ServiceConnection.domain_id == principal.domain_id,
                ServiceConnection.name == body.model,
            )
            .with_for_update()
        )
        if row is None:
            raise APIError(404, "resource_not_found", "Service not found")
        if key_hash:
            previous = session.scalar(
                select(Invocation).where(
                    Invocation.scope == scope, Invocation.key_hash.in_(candidates)
                )
            )
            if previous:
                error = APIError(
                    409,
                    "duplicate_invocation"
                    if hmac.compare_digest(previous.input_hash, candidates[previous.key_hash])
                    else "idempotency_conflict",
                    "Invocation is not replayable",
                )
                error.headers = {"X-Invocation-ID": previous.id}
                raise error
        if not row.enabled:
            raise APIError(409, "service_disabled", "Service has not been enabled")
        credential = session.get(NodeCredential, row.node_id)
        if credential is None or not credential.enabled:
            raise APIError(409, "node_disabled", "Node is not enabled")
        call = Invocation(
            id=uuid4().hex,
            domain_id=principal.domain_id,
            service_id=row.id,
            scope=scope,
            key_hash=key_hash,
            input_hash=digest,
            hmac_key_version=key_version,
            state="admitted",
            owner_instance_id=app.state.node_hub.instance_id,
            deadline=datetime.now(UTC) + timedelta(seconds=60),
        )
        session.add(call)
        session.commit()
        return row, call.id


def mark_running(app, call_id):
    with Session(app.state.engine) as session, session.begin():
        session.execute(
            update(Invocation)
            .where(Invocation.id == call_id, Invocation.state == "admitted")
            .values(state="running")
        )


def finish(app, call_id, state, usage=None):
    with Session(app.state.engine) as session:
        row = session.get(Invocation, call_id, with_for_update=True)
        if row and row.state in {"admitted", "running"}:
            row.state, row.completed_at = state, datetime.now(UTC)
            row.usage = usage
            session.commit()


def late_fact(app, node_id, message):
    with Session(app.state.engine) as session:
        row = session.scalar(
            select(Invocation)
            .join(ServiceConnection)
            .where(Invocation.id == message["invocation_id"], ServiceConnection.node_id == node_id)
            .with_for_update(of=Invocation)
        )
        if row and row.state == "unknown" and row.late_report is None:
            # An authenticated report is evidence, not verified output or a state transition.
            row.late_report = {
                "kind": "node_reported_completion",
                "node_id": node_id,
                "received_at": datetime.now(UTC).isoformat(),
            }
            session.commit()


def expire_metadata(app):
    now = datetime.now(UTC)
    with Session(app.state.engine) as session, session.begin():
        session.execute(
            update(Invocation)
            .where(Invocation.state.in_(["admitted", "running"]), Invocation.deadline <= now)
            .values(state="unknown", completed_at=now)
        )
        # Deduplication is explicitly bounded to the same 30-day metadata window.
        session.execute(delete(Invocation).where(Invocation.created_at < now - timedelta(days=30)))


def check_active(app, principal, service_id):
    if "service:invoke" not in revalidate(app, principal):
        raise APIError(403, "forbidden", "Service grant revoked")
    with Session(app.state.engine) as session:
        service = session.get(ServiceConnection, service_id)
        credential = session.get(NodeCredential, service.node_id) if service else None
        if not service or not service.enabled or not credential or not credential.enabled:
            raise APIError(403, "service_revoked", "Service or Node disabled")


@router.get(
    "/invocations/{call_id}", operation_id="getInvocationMetadata", response_model=InvocationView
)
def metadata(call_id: str, principal: Authenticated, session: Database):
    principal.require("service:invoke")
    row = session.scalar(
        select(Invocation)
        .where(
            Invocation.id == call_id,
            Invocation.domain_id == principal.domain_id,
            Invocation.scope == scope_for(principal),
        )
        .with_for_update()
    )
    if row is None:
        raise APIError(404, "resource_not_found", "Invocation not found")
    if row.state in {"admitted", "running"} and row.deadline < datetime.now(UTC):
        row.state, row.completed_at = "unknown", datetime.now(UTC)
        session.commit()
    return {
        "id": row.id,
        "state": row.state,
        "service_id": row.service_id,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
        "usage": row.usage,
        "late_report": row.late_report,
        "result_replay": False,
    }


@router.post(
    "/chat/completions",
    operation_id="streamLocalChat",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Request-scoped text SSE; not resumable",
            "headers": {"X-Invocation-ID": {"schema": {"type": "string"}}},
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def chat(
    body: ChatRequest,
    request: Request,
    principal: Authenticated,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=200)] = None,
):
    app = request.app
    principal.require("service:invoke")
    service, call_id = await anyio.to_thread.run_sync(admit, app, principal, body, idempotency_key)
    try:
        connection = app.state.node_hub.get(service.node_id)
        if sum(len(c.streams) for c in app.state.node_hub.connections.values()) >= 32:
            raise APIError(429, "platform_busy", "Platform inference capacity reached")
        if len(connection.streams) >= 2:
            raise APIError(429, "node_busy", "Node inference capacity reached")
        queue = asyncio.Queue(32)
        connection.streams[call_id] = queue
        await anyio.to_thread.run_sync(mark_running, app, call_id)
        await connection.send(
            {
                "type": "inference",
                "invocation_id": call_id,
                "local_ref": service.local_ref,
                "request": body.model_dump() | {"model": service.model},
            },
            data=True,
        )
    except (APIError, TimeoutError, SQLAlchemyError) as error:
        if "connection" in locals():
            connection.streams.pop(call_id, None)
        try:
            await anyio.to_thread.run_sync(
                finish, app, call_id, "unknown" if isinstance(error, TimeoutError) else "failed"
            )
        except SQLAlchemyError:
            pass  # The metadata expiry pass will resolve the record when the DB recovers.
        if isinstance(error, TimeoutError):
            raise APIError(503, "node_backpressure", "Node queue is full") from None
        raise

    async def output():
        status, usage = "unknown", None
        checked_at, output_bytes = 0.0, 0
        try:
            async with asyncio.timeout(60):
                while not connection.closed:
                    now = asyncio.get_running_loop().time()
                    if now - checked_at >= 1:
                        await anyio.to_thread.run_sync(check_active, app, principal, service.id)
                        checked_at = now
                    try:
                        message = await asyncio.wait_for(queue.get(), 1)
                    except TimeoutError:
                        if await request.is_disconnected():
                            break
                        continue
                    if message["type"] == "delta":
                        content = message.get("content")
                        if not isinstance(content, str) or len(content.encode()) > 16384:
                            break
                        output_bytes += len(content.encode())
                        if output_bytes > 2 * 1024 * 1024:
                            break
                        chunk = {
                            "id": call_id,
                            "object": "chat.completion.chunk",
                            "created": int(datetime.now(UTC).timestamp()),
                            "model": body.model,
                            "choices": [
                                {"index": 0, "delta": {"content": content}, "finish_reason": None}
                            ],
                        }
                        yield "data: " + json.dumps(chunk) + "\n\n"
                    elif message["type"] == "complete":
                        reason = message.get("finish_reason", "stop")
                        if reason not in {"stop", "length"}:
                            break
                        status = "succeeded"
                        raw_usage = message.get("usage")
                        if isinstance(raw_usage, dict) and all(
                            type(raw_usage.get(k)) is int and 0 <= raw_usage[k] <= 10**9
                            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                        ):
                            usage = {
                                k: raw_usage[k]
                                for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                            }
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "id": call_id,
                                    "object": "chat.completion.chunk",
                                    "model": body.model,
                                    "created": int(datetime.now(UTC).timestamp()),
                                    "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
                                }
                            )
                            + "\n\n"
                        )
                        yield "data: [DONE]\n\n"
                        return
                    else:
                        break
        except (TimeoutError, APIError):
            pass
        finally:
            connection.streams.pop(call_id, None)
            with anyio.CancelScope(shield=True):
                try:
                    await connection.send({"type": "cancel", "invocation_id": call_id})
                except (TimeoutError, APIError):
                    pass
                await anyio.to_thread.run_sync(finish, app, call_id, status, usage)
        yield 'data: {"error":{"code":"execution_unknown","message":"Stream interrupted"}}\n\n'

    return StreamingResponse(
        output(),
        media_type="text/event-stream",
        headers={"X-Invocation-ID": call_id, "X-Accel-Buffering": "no"},
    )

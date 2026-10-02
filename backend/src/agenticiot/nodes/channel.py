"""Single-process async transport with durable leases and bounded queues."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from agenticiot.access.models import Domain
from agenticiot.nodes.identity import authenticate_node
from agenticiot.nodes.models import NodeCredential, NodeSession
from agenticiot.runtime.models import Command, EdgeNode, Receipt
from agenticiot.runtime.schemas import EdgeRegistration, ObservationInput, ResultReport
from agenticiot.runtime.service import EdgeService
from agenticiot.security import APIError

router = APIRouter()


@dataclass
class Connection:
    node_id: str
    epoch: int
    control: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(64))
    data: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(32))
    streams: dict = field(default_factory=dict)
    commands: set = field(default_factory=set)
    closed: bool = False

    async def send(self, message, *, data=False):
        if self.closed:
            raise APIError(503, "node_offline", "Node disconnected")
        await asyncio.wait_for((self.data if data else self.control).put(message), 2)


class Hub:
    def __init__(self):
        self.instance_id = uuid4().hex
        self.connections: dict[str, Connection] = {}
        self.draining = False

    def get(self, node_id):
        connection = self.connections.get(node_id)
        if self.draining or connection is None or connection.closed:
            raise APIError(503, "node_offline", "No active Node connection")
        return connection

    async def close(self):
        self.draining = True
        # Bounded drain; metadata completion continues while connected calls finish.
        for _ in range(50):
            if not any(c.streams or c.commands for c in self.connections.values()):
                break
            await asyncio.sleep(0.1)
        for connection in list(self.connections.values()):
            connection.closed = True


def acquire(app, token, registration):
    principal = authenticate_node(app, token)
    with Session(app.state.engine, expire_on_commit=False) as session:
        service = EdgeService(session, principal, uuid4().hex)
        node = service.register(registration)
        # Lock the stable node row, including the first session insert.
        session.scalar(select(EdgeNode).where(EdgeNode.id == node.id).with_for_update())
        lease = session.get(NodeSession, node.id)
        if lease is None:
            lease = NodeSession(node_id=node.id, epoch=0)
            session.add(lease)
        lease.epoch += 1
        lease.owner_instance_id = app.state.node_hub.instance_id
        lease.expires_at = datetime.now(UTC) + timedelta(seconds=15)
        session.commit()
        return principal, node.id, lease.epoch


def transaction(app, principal, connection, kind, payload=None):
    with Session(app.state.engine, expire_on_commit=False) as session:
        # Match acquire's lock order so reconnect and evidence ingestion cannot deadlock.
        session.scalar(select(EdgeNode).where(EdgeNode.id == connection.node_id).with_for_update())
        lease = session.scalar(
            select(NodeSession).where(NodeSession.node_id == connection.node_id).with_for_update()
        )
        if (
            lease is None
            or lease.epoch != connection.epoch
            or lease.owner_instance_id != app.state.node_hub.instance_id
            or lease.expires_at <= datetime.now(UTC)
        ):
            raise APIError(409, "stale_session", "Node session no longer owns dispatch")
        credential = session.get(NodeCredential, connection.node_id)
        if (
            not credential
            or not credential.enabled
            or not session.get(Domain, principal.domain_ref).enabled
        ):
            raise APIError(403, "node_disabled", "Node or domain disabled")
        service = EdgeService(session, principal, uuid4().hex)
        if kind == "heartbeat":
            lease.expires_at = datetime.now(UTC) + timedelta(seconds=15)
            service.edge()
            session.commit()
            return None
        if kind == "bindings":
            from agenticiot.runtime.schemas import BindingView

            result = [
                BindingView.model_validate(b).model_dump(mode="json") for b in service.assigned()
            ]
            session.commit()
            return result
        if kind == "observation":
            return service.upload(
                payload["thing_id"], ObservationInput.model_validate(payload["data"])
            )
        if kind == "result":
            return service.result(
                payload["command_id"], TypeAdapter(ResultReport).validate_python(payload["data"])
            ).model_dump(mode="json")
        if kind == "dispatch":
            commands = session.scalars(
                select(Command)
                .where(
                    Command.edge_id == connection.node_id,
                    Command.domain_ref == principal.domain_ref,
                    or_(
                        Command.stage.in_(["accepted", "dispatched", "timed_out"]),
                        Command.id.in_(
                            select(Receipt.command_id).where(
                                Receipt.error_code == "execution_uncertain"
                            )
                        ),
                    ),
                )
                .order_by(Command.created_at, Command.id)
                .with_for_update()
            ).all()
            payload.intersection_update(c.id for c in commands)
            selected, busy = [], set()
            for command in commands:
                service.settle(command)
                if command.thing_id in busy:
                    continue
                busy.add(command.thing_id)
                # Uncertain predecessors fence this binding; never blindly advance it.
                if command.stage in {"timed_out", "failed"} or command.id in payload:
                    continue
                if command.stage == "accepted":
                    service.receipt(command, "dispatched", executor=principal.edge_ref)
                if command.stage == "dispatched":
                    selected.append(service.command_view(command).model_dump(mode="json"))
                if len(selected) >= 8:
                    break
            session.commit()
            return selected
        raise APIError(422, "invalid_message", "Unsupported Node message")


@router.websocket("/v1/nodes/channel")
async def channel(websocket: WebSocket):
    app = websocket.app
    token = websocket.headers.get("authorization", "").removeprefix("Bearer ")
    tasks = []
    connection = None
    try:
        if app.state.node_hub.draining:
            await websocket.close(code=1013)
            return
        await anyio.to_thread.run_sync(authenticate_node, app, token)
        await websocket.accept()
        raw = await asyncio.wait_for(websocket.receive_text(), 10)
        if len(raw.encode()) > 16384:
            raise ValueError("Hello too large")
        hello = json.loads(raw)
        if not isinstance(hello, dict) or hello.get("type") != "hello" or hello.get("version") != 1:
            raise ValueError("Unsupported protocol")
        principal, node_id, epoch = await anyio.to_thread.run_sync(
            acquire, app, token, EdgeRegistration.model_validate(hello["registration"])
        )
        connection = Connection(node_id, epoch)
        previous = app.state.node_hub.connections.get(node_id)
        if previous:
            previous.closed = True
        app.state.node_hub.connections[node_id] = connection
        await websocket.send_json({"type": "welcome", "node_id": node_id, "epoch": epoch})

        async def writer():
            while not connection.closed:
                try:
                    message = connection.control.get_nowait()
                except asyncio.QueueEmpty:
                    try:
                        message = await asyncio.wait_for(connection.data.get(), 0.05)
                    except TimeoutError:
                        continue
                await asyncio.wait_for(websocket.send_json(message), 5)

        async def maintenance():
            sent, tick, bindings = set(), 0, None
            while not connection.closed:
                if tick % 50 == 0:
                    await anyio.to_thread.run_sync(authenticate_node, app, token)
                    await anyio.to_thread.run_sync(
                        transaction, app, principal, connection, "heartbeat"
                    )
                    new = await anyio.to_thread.run_sync(
                        transaction, app, principal, connection, "bindings"
                    )
                    if new != bindings:
                        await connection.send({"type": "bindings", "items": new})
                        bindings = new
                if not app.state.node_hub.draining:
                    commands = await anyio.to_thread.run_sync(
                        transaction, app, principal, connection, "dispatch", sent
                    )
                    for command in commands:
                        await connection.send({"type": "command", "command": command})
                        sent.add(command["id"])
                        connection.commands.add(command["id"])
                tick += 1
                await asyncio.sleep(0.1)

        async def reader():
            while not connection.closed:
                raw = await asyncio.wait_for(websocket.receive_text(), 20)
                if len(raw.encode()) > 262144:
                    raise ValueError("Message too large")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("Object required")
                kind = message.get("type")
                if kind == "ping":
                    continue
                if kind in {"observation", "result"}:
                    try:
                        await anyio.to_thread.run_sync(
                            transaction, app, principal, connection, kind, message
                        )
                        if kind == "result":
                            connection.commands.discard(message["command_id"])
                        await connection.send({"type": "ack", "sequence": message["sequence"]})
                    except (APIError, ValidationError) as error:
                        await connection.send(
                            {
                                "type": "reject",
                                "sequence": message.get("sequence"),
                                "code": getattr(error, "code", "invalid_message"),
                            }
                        )
                elif kind in {"delta", "complete", "inference_error"}:
                    call_id = message.get("invocation_id")
                    if not isinstance(call_id, str) or len(call_id) != 32:
                        raise ValueError("Invalid invocation ID")
                    queue = connection.streams.get(call_id)
                    if queue is not None:
                        try:
                            queue.put_nowait(message)
                        except asyncio.QueueFull:
                            connection.streams.pop(call_id, None)
                            while not queue.empty():
                                queue.get_nowait()
                            queue.put_nowait({"type": "inference_error"})
                            await connection.send({"type": "cancel", "invocation_id": call_id})
                    elif kind == "complete":
                        from agenticiot.services.api import late_fact

                        await anyio.to_thread.run_sync(late_fact, app, node_id, message)
                else:
                    raise ValueError("Unsupported message")

        tasks = [asyncio.create_task(fn()) for fn in (writer, maintenance, reader)]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, APIError, ValidationError, ValueError, KeyError, TimeoutError):
        pass
    finally:
        if connection:
            connection.closed = True
            if app.state.node_hub.connections.get(connection.node_id) is connection:
                del app.state.node_hub.connections[connection.node_id]
        with anyio.CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await websocket.close(code=1001)
            except (RuntimeError, WebSocketDisconnect):
                pass

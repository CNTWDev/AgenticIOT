"""Single-process async transport with durable leases and bounded queues."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from agenticiot.access.models import Domain
from agenticiot.nodes.identity import authenticate_node
from agenticiot.nodes.models import NodeCredential, NodeSession
from agenticiot.runtime.models import Command, EdgeNode
from agenticiot.runtime.schemas import EdgeRegistration, ObservationInput, ResultReport
from agenticiot.runtime.service import EdgeService
from agenticiot.security import APIError

router = APIRouter()
logger = logging.getLogger(__name__)


@dataclass
class Connection:
    node_id: str
    epoch: int
    control: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(64))
    data: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(32))
    streams: dict = field(default_factory=dict)
    commands: set = field(default_factory=set)
    closed: bool = False
    capacity: int = 16
    clock_offset: float | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    journal_stats: dict = field(default_factory=dict)
    ready: asyncio.Event = field(default_factory=asyncio.Event)

    async def send(self, message, *, data=False):
        if self.closed:
            raise APIError(503, "node_offline", "Node disconnected")
        await asyncio.wait_for((self.data if data else self.control).put(message), 2)
        self.ready.set()


class Hub:
    def __init__(self):
        self.instance_id = uuid4().hex
        self.connections: dict[str, Connection] = {}
        self.draining = False
        self.loop = asyncio.get_running_loop()

    def notify(self, node_id):
        def wake():
            connection = self.connections.get(node_id)
            if connection is not None:
                connection.wake.set()

        self.loop.call_soon_threadsafe(wake)

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
        service = EdgeService(session, principal, uuid4().hex, clock_offset=connection.clock_offset)
        if kind in {"received", "reconciled"}:
            command = service.locked(Command, payload["command_id"])
            if command.edge_id != connection.node_id:
                raise APIError(404, "resource_not_found", "Assigned command not found")
            if kind == "received":
                if command.received_at is None:
                    command.received_at = datetime.now(UTC)
            elif command.blocked and command.barrier_at is None:
                outcome = payload["data"].get("outcome")
                if outcome not in {"not_executed", "uncertain"}:
                    raise APIError(422, "invalid_message", "Invalid reconciliation outcome")
                if outcome == "not_executed" and command.result_hash is not None:
                    raise APIError(
                        409, "result_conflict", "A saved result prevents a nonexecution claim"
                    )
                command.barrier_at = datetime.now(UTC)
                if outcome == "not_executed":
                    command.blocked = False
                    service.receipt(
                        command,
                        "failed",
                        error="not_executed",
                        evidence={"kind": "durable_node_tombstone"},
                    )
                else:
                    service.receipt(
                        command, command.stage, evidence={"kind": "durable_node_tombstone"}
                    )
            session.commit()
            return None
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
                        Command.stage.in_(["accepted", "dispatched"]),
                        Command.blocked.is_(True),
                    ),
                )
                .order_by(Command.created_at, Command.id)
                .with_for_update()
            ).all()
            payload.intersection_update(c.id for c in commands)
            connection.commands.intersection_update(payload)
            available = max(0, connection.capacity - len(payload))
            selected, busy = [], set()
            for command in commands:
                service.settle(command)
                if command.thing_id in busy:
                    continue
                busy.add(command.thing_id)
                # Uncertain predecessors fence this binding; never blindly advance it.
                if command.blocked:
                    if command.barrier_at is None:
                        selected.append({"reconcile": command.id})
                    continue
                if command.stage not in {"accepted", "dispatched"} or command.id in payload:
                    continue
                if available <= 0:
                    continue
                if command.stage == "accepted":
                    command.dispatch_sequence = session.get(
                        EdgeNode, connection.node_id
                    ).last_sequence
                    service.receipt(command, "dispatched", executor=principal.edge_ref)
                if command.stage == "dispatched":
                    selected.append(service.command_view(command).model_dump(mode="json"))
                    available -= 1
                if len(selected) >= 8:
                    break
            session.commit()
            if selected:
                logger.info(
                    "Node dispatch: node=%s selected=%d blocked=%d inflight=%d",
                    connection.node_id,
                    len(selected),
                    sum(c.blocked for c in commands),
                    len(payload),
                )
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
            if "websocket.http.response" in websocket.scope.get("extensions", {}):
                await websocket.send_denial_response(
                    JSONResponse(
                        {"code": "platform_draining"}, status_code=503, headers={"Retry-After": "5"}
                    )
                )
            else:
                await websocket.close(code=1013)
            return
        await anyio.to_thread.run_sync(authenticate_node, app, token)
        await websocket.accept()
        raw = await asyncio.wait_for(websocket.receive_text(), 10)
        if len(raw.encode()) > 16384:
            raise ValueError("Hello too large")
        hello = json.loads(raw)
        if (
            not isinstance(hello, dict)
            or hello.get("type") != "hello"
            or hello.get("version") not in {1, 2}
        ):
            raise ValueError("Unsupported protocol")
        principal, node_id, epoch = await anyio.to_thread.run_sync(
            acquire, app, token, EdgeRegistration.model_validate(hello["registration"])
        )
        connection = Connection(node_id, epoch)
        if hello.get("version") == 2:
            if type(hello.get("capacity")) is not int or not 1 <= hello["capacity"] <= 16:
                raise ValueError("Invalid capacity")
            connection.capacity = hello["capacity"]
            node_time = datetime.fromisoformat(hello["node_time"])
            connection.clock_offset = (datetime.now(UTC) - node_time).total_seconds()
            logger.info(
                "Node clock estimate: node=%s offset_seconds=%.3f", node_id, connection.clock_offset
            )
        previous = app.state.node_hub.connections.get(node_id)
        if previous:
            previous.closed = True
        app.state.node_hub.connections[node_id] = connection
        await websocket.send_json(
            {
                "type": "welcome",
                "node_id": node_id,
                "epoch": epoch,
                "server_time": datetime.now(UTC).isoformat(),
            }
        )
        sent = set()

        async def writer():
            while not connection.closed:
                connection.ready.clear()
                try:
                    message = connection.control.get_nowait()
                except asyncio.QueueEmpty:
                    try:
                        message = connection.data.get_nowait()
                    except asyncio.QueueEmpty:
                        await connection.ready.wait()
                        continue
                await asyncio.wait_for(websocket.send_json(message), 5)

        async def maintenance():
            bindings = None
            while not connection.closed:
                connection.wake.clear()
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
                        if "reconcile" in command:
                            await connection.send(
                                {"type": "reconcile", "command_id": command["reconcile"]}
                            )
                            continue
                        await connection.send({"type": "command", "command": command})
                        sent.add(command["id"])
                        connection.commands.add(command["id"])
                try:
                    await asyncio.wait_for(connection.wake.wait(), 2)
                except TimeoutError:
                    pass

        async def heartbeat():
            while not connection.closed:
                await anyio.to_thread.run_sync(authenticate_node, app, token)
                await anyio.to_thread.run_sync(transaction, app, principal, connection, "heartbeat")
                await asyncio.sleep(5)

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
                    stats = message.get("journal", {})
                    if isinstance(stats, dict) and all(
                        type(stats.get(k)) is int and 0 <= stats[k] <= 10**9
                        for k in ("pending", "quarantined")
                    ):
                        connection.journal_stats = stats
                    continue
                if kind == "busy":
                    sent.discard(message["command_id"])
                    connection.commands.discard(message["command_id"])
                    continue
                if kind in {"observation", "result", "received", "reconciled"}:
                    try:
                        await anyio.to_thread.run_sync(
                            transaction, app, principal, connection, kind, message
                        )
                        if kind in {"result", "reconciled"}:
                            sent.discard(message["command_id"])
                            connection.commands.discard(message["command_id"])
                            connection.wake.set()
                        if kind != "received":
                            await connection.send({"type": "ack", "sequence": message["sequence"]})
                    except (APIError, ValidationError) as error:
                        if kind == "received":
                            raise
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

        tasks = [asyncio.create_task(fn()) for fn in (writer, maintenance, heartbeat, reader)]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (
        WebSocketDisconnect,
        APIError,
        ValidationError,
        ValueError,
        TypeError,
        KeyError,
        TimeoutError,
        SQLAlchemyError,
    ) as error:
        logger.info("Node channel ended: %s", type(error).__name__)
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

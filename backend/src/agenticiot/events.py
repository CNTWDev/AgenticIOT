"""Bounded, domain-scoped replay of ingested sampled state changes."""

import asyncio
import json
from datetime import datetime
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from agenticiot.access.api import Database
from agenticiot.database import Base
from agenticiot.registry.schemas import ResourceID, SchemaModel
from agenticiot.security import APIError, Authenticated, revalidate

RETENTION = 1000
router = APIRouter(prefix="/v1/events", tags=["Captured device events"])


class CapturedEvent(SchemaModel):
    id: str
    thing_id: ResourceID
    source_kind: Literal["sampled_change"]
    data: dict
    received_at: datetime


class EventPage(SchemaModel):
    items: list[CapturedEvent]
    cursor: str
    retained_events: int
    complete_physical_history: Literal[False]


class EventCursor(Base):
    __tablename__ = "device_event_cursors"
    domain_id: Mapped[str] = mapped_column(ForeignKey("access_domains.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger)


class DeviceEvent(Base):
    __tablename__ = "device_events"
    domain_id: Mapped[str] = mapped_column(ForeignKey("access_domains.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thing_id: Mapped[str] = mapped_column(ForeignKey("registry_devices.id"))
    source_kind: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def capture_change(session, domain_id, previous, observation):
    if previous is None or previous.values == observation.values:
        return
    if observation.observed_at < previous.observed_at:
        return
    session.execute(
        insert(EventCursor).values(domain_id=domain_id, sequence=0).on_conflict_do_nothing()
    )
    cursor = session.get(EventCursor, domain_id, with_for_update=True)
    # The per-domain cursor lock is held until the observation transaction commits.
    cursor.sequence += 1
    session.add(
        DeviceEvent(
            domain_id=domain_id,
            sequence=cursor.sequence,
            thing_id=observation.thing_id,
            source_kind="sampled_change",
            data={
                "before": previous.values,
                "after": observation.values,
                "window_start": previous.observed_at.isoformat(),
                "window_end": observation.observed_at.isoformat(),
                "source_ref": observation.source_ref,
                "observation_id": observation.id,
                "complete_physical_history": False,
            },
        )
    )
    session.execute(
        delete(DeviceEvent).where(
            DeviceEvent.domain_id == domain_id, DeviceEvent.sequence <= cursor.sequence - RETENTION
        )
    )


def page(session, principal, cursor):
    principal.require("events:read")
    after = 0
    if cursor:
        try:
            domain, number = cursor.split(":")
            after = int(number)
            if domain != principal.domain_id or after < 0:
                raise ValueError
        except (ValueError, TypeError):
            raise APIError(400, "invalid_cursor", "Cursor is outside this domain") from None
    head = session.get(EventCursor, principal.domain_id)
    head = head.sequence if head else 0
    if after > head:
        raise APIError(400, "invalid_cursor", "Cursor is ahead of captured events")
    if cursor and after < head - RETENTION:
        raise APIError(409, "resync_required", "Event cursor is outside retained history")
    # A fresh subscription starts at retained history, never claims complete history.
    after = max(after, head - RETENTION)
    rows = session.scalars(
        select(DeviceEvent)
        .where(
            DeviceEvent.domain_id == principal.domain_id,
            DeviceEvent.sequence > after,
            DeviceEvent.sequence <= head,
        )
        .order_by(DeviceEvent.sequence)
        .limit(100)
    ).all()
    return {
        "items": [
            {
                "id": f"{r.domain_id}:{r.sequence}",
                "thing_id": r.thing_id,
                "source_kind": r.source_kind,
                "data": r.data,
                "received_at": r.received_at.isoformat(),
            }
            for r in rows
        ],
        "cursor": f"{principal.domain_id}:{rows[-1].sequence if rows else after}",
        "retained_events": RETENTION,
        "complete_physical_history": False,
    }


@router.get("/page", operation_id="readCapturedEvents", response_model=EventPage)
def events_page(
    principal: Authenticated,
    session: Database,
    cursor: Annotated[str | None, Query(max_length=100)] = None,
):
    return page(session, principal, cursor)


@router.get(
    "",
    operation_id="subscribeCapturedEvents",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Captured device events with Last-Event-ID replay",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def subscribe(
    request: Request,
    principal: Authenticated,
    last_event_id: Annotated[str | None, Header(max_length=100)] = None,
):
    principal.require("events:read")

    def read(cursor):
        allowed = revalidate(request.app, principal)
        if "events:read" not in allowed:
            raise APIError(403, "forbidden", "Event authorization revoked")
        with Session(request.app.state.engine) as session:
            return page(session, principal, cursor)

    first = await anyio.to_thread.run_sync(read, last_event_id)

    async def stream():
        result = first
        while not await request.is_disconnected():
            for event in result["items"]:
                yield f"id: {event['id']}\nevent: device_event\ndata: {json.dumps(event)}\n\n"
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
            try:
                result = await anyio.to_thread.run_sync(read, result["cursor"])
            except APIError as error:
                yield "event: stream_error\ndata: " + json.dumps({"code": error.code}) + "\n\n"
                return

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
    )

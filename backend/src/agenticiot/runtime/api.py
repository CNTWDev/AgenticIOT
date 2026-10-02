from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from agenticiot.registry.schemas import Page, SchemaModel
from agenticiot.runtime.models import EdgeNode
from agenticiot.runtime.schemas import (
    BindingCreate,
    BindingView,
    CommandView,
    EdgeRegistration,
    EdgeView,
    InvokeRequest,
    ObservationInput,
    ReceiptView,
    ResultReport,
    ThingState,
)
from agenticiot.runtime.service import EdgeService, RuntimeService
from agenticiot.security import Actuator, APIError, Authenticated, EdgeAuthenticated, Operator

router = APIRouter(prefix="/v1")
legacy_router = APIRouter(prefix="/v1")


def runtime(request: Request, principal: Authenticated):
    principal.require("device:read")
    if request.app.state.engine is None:
        raise APIError(503, "database_not_ready", "Database is not ready")
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield RuntimeService(session, principal, request.state.trace_id)


def edge_runtime(request: Request, principal: EdgeAuthenticated):
    if request.app.state.engine is None:
        raise APIError(503, "database_not_ready", "Database is not ready")
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield EdgeService(session, principal, request.state.trace_id)


Runtime = Annotated[RuntimeService, Depends(runtime)]
EdgeRuntime = Annotated[EdgeService, Depends(edge_runtime)]


@router.get(
    "/management/edges", response_model=Page[EdgeView], tags=["Edges"], operation_id="listEdges"
)
def list_edges(service: Runtime):
    rows = service.session.scalars(
        select(EdgeNode)
        .where(EdgeNode.domain_ref == service.domain)
        .order_by(EdgeNode.id)
        .limit(200)
    ).all()
    return {"items": rows}


@router.put(
    "/management/devices/{thing_id}/binding",
    response_model=BindingView,
    tags=["Edges"],
    operation_id="bindVirtualDevice",
)
def bind_device(thing_id: str, data: BindingCreate, _operator: Operator, service: Runtime):
    return service.bind(thing_id, data)


@router.get(
    "/things/{thing_id}/binding",
    response_model=BindingView | None,
    tags=["Things"],
    operation_id="getThingBinding",
)
def get_binding(thing_id: str, service: Runtime):
    return service.binding(thing_id)


@router.get(
    "/things/{thing_id}/state",
    response_model=ThingState,
    tags=["Things"],
    operation_id="getThingState",
)
def get_state(thing_id: str, service: Runtime, fresh: bool = False):
    if fresh:
        raise APIError(
            501, "fresh_read_unavailable", "Synchronous physical reads are not supported"
        )
    return service.state(thing_id)


@router.post(
    "/things/{thing_id}/actions/{action_name}",
    response_model=CommandView,
    status_code=202,
    tags=["Commands"],
    operation_id="invokeThingAction",
)
def invoke(
    thing_id: str,
    action_name: str,
    data: InvokeRequest,
    request: Request,
    response: Response,
    _operator: Actuator,
    service: Runtime,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
):
    command = service.invoke(thing_id, action_name, data, idempotency_key)
    request.app.state.node_hub.notify(service.binding(command.thing_id).edge_id)
    response.headers["Location"] = f"/v1/commands/{command.id}"
    return command


@router.get(
    "/things/{thing_id}/commands",
    response_model=list[CommandView],
    tags=["Commands"],
    operation_id="listThingCommands",
)
def list_commands(thing_id: str, service: Runtime):
    return service.commands(thing_id)


@router.get(
    "/commands/{command_id}",
    response_model=CommandView,
    tags=["Commands"],
    operation_id="getCommand",
)
def get_command(command_id: str, service: Runtime):
    return service.read_command(command_id)


class ReconciliationInput(SchemaModel):
    observation_id: str = Field(min_length=32, max_length=32)
    reason: str = Field(min_length=10, max_length=1000)


@router.post(
    "/commands/{command_id}/reconcile",
    response_model=CommandView,
    tags=["Commands"],
    operation_id="reconcileCommand",
)
def reconcile(
    command_id: str,
    data: ReconciliationInput,
    request: Request,
    _operator: Operator,
    service: Runtime,
):
    result = service.reconcile(command_id, data.observation_id, data.reason)
    request.app.state.node_hub.notify(service.binding(result.thing_id).edge_id)
    return result


@router.get(
    "/commands/{command_id}/receipts",
    response_model=Page[ReceiptView],
    tags=["Commands"],
    operation_id="listCommandReceipts",
)
def receipts(command_id: str, service: Runtime):
    return {"items": service.read_command(command_id).receipts}


@legacy_router.post(
    "/edge/register", response_model=EdgeView, tags=["Edge channel"], operation_id="registerEdge"
)
def register_edge(data: EdgeRegistration, service: EdgeRuntime):
    return service.register(data)


@legacy_router.get(
    "/edge/bindings",
    response_model=Page[BindingView],
    tags=["Edge channel"],
    operation_id="listAssignedBindings",
)
def assigned(service: EdgeRuntime):
    return {"items": service.assigned()}


@legacy_router.post(
    "/edge/commands/claim",
    response_model=CommandView | None,
    tags=["Edge channel"],
    operation_id="claimEdgeCommand",
)
def claim(service: EdgeRuntime):
    return service.claim()


@legacy_router.post(
    "/edge/things/{thing_id}/observations",
    tags=["Edge channel"],
    operation_id="uploadEdgeObservation",
)
def observation(
    thing_id: str, data: ObservationInput, service: EdgeRuntime
) -> dict[str, str | int]:
    return service.upload(thing_id, data)


@legacy_router.post(
    "/edge/commands/{command_id}/result",
    response_model=CommandView,
    tags=["Edge channel"],
    operation_id="uploadEdgeResult",
)
def result(command_id: str, data: ResultReport, service: EdgeRuntime):
    return service.result(command_id, data)

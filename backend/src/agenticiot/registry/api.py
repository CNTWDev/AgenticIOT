from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from agenticiot.registry.models import Device, DeviceModel, RegistryAudit
from agenticiot.registry.schemas import (
    AuditView,
    DeviceCreate,
    DevicePatch,
    DeviceView,
    ModelCreate,
    ModelView,
    Page,
    ThingView,
)
from agenticiot.registry.service import RegistryService
from agenticiot.runtime.service import device_view
from agenticiot.security import APIError, Authenticated, Operator

router = APIRouter(prefix="/v1")


def registry(request: Request, principal: Authenticated):
    principal.require("device:read")
    engine = request.app.state.engine
    if engine is None:
        raise APIError(503, "database_not_ready", "Database is not ready")
    with Session(engine, expire_on_commit=False) as session:
        yield RegistryService(session, principal, request.state.trace_id)


Service = Annotated[RegistryService, Depends(registry)]
Limit = Annotated[int, Query(ge=1, le=200)]
Cursor = Annotated[str | None, Query(max_length=2000)]


@router.get("/management/session", tags=["Management"], operation_id="getSession")
def get_session(principal: Authenticated) -> dict[str, str]:
    return {
        "subject_ref": principal.subject_ref,
        "domain_ref": principal.domain_ref,
        "role": principal.role,
    }


@router.post(
    "/management/models",
    response_model=ModelView,
    status_code=201,
    tags=["Models"],
    operation_id="publishModel",
)
def publish_model(data: ModelCreate, response: Response, _operator: Operator, service: Service):
    model = service.publish_model(data)
    response.headers["Location"] = f"/v1/management/models/{model.id}"
    return model


@router.get(
    "/management/models", response_model=Page[ModelView], tags=["Models"], operation_id="listModels"
)
def list_models(service: Service, limit: Limit = 50, cursor: Cursor = None):
    rows, next_cursor = service.page(DeviceModel, limit, cursor)
    return {"items": rows, "next_cursor": next_cursor}


@router.get(
    "/management/models/{model_id}",
    response_model=ModelView,
    tags=["Models"],
    operation_id="getModel",
)
def get_model(model_id: str, service: Service):
    return service.get(DeviceModel, model_id)


@router.post(
    "/management/devices",
    response_model=DeviceView,
    status_code=201,
    tags=["Devices"],
    operation_id="registerDevice",
)
def register_device(data: DeviceCreate, response: Response, _operator: Operator, service: Service):
    device = service.register_device(data)
    response.headers["Location"] = f"/v1/management/devices/{device.id}"
    response.headers["ETag"] = f'"{device.revision}"'
    return device_view(service.session, device)


@router.get(
    "/management/devices",
    response_model=Page[DeviceView],
    tags=["Devices"],
    operation_id="listDevices",
)
def list_devices(service: Service, limit: Limit = 50, cursor: Cursor = None):
    rows, next_cursor = service.page(Device, limit, cursor)
    return {
        "items": [device_view(service.session, row) for row in rows],
        "next_cursor": next_cursor,
    }


@router.get(
    "/management/devices/{device_id}",
    response_model=DeviceView,
    tags=["Devices"],
    operation_id="getDevice",
)
def get_device(device_id: str, service: Service, response: Response):
    device = service.get(Device, device_id)
    response.headers["ETag"] = f'"{device.revision}"'
    return device_view(service.session, device)


@router.patch(
    "/management/devices/{device_id}",
    response_model=DeviceView,
    tags=["Devices"],
    operation_id="editDevice",
)
def edit_device(
    device_id: str,
    data: DevicePatch,
    _operator: Operator,
    service: Service,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
):
    device = service.edit_device(device_id, data, if_match)
    response.headers["ETag"] = f'"{device.revision}"'
    return device_view(service.session, device)


@router.get(
    "/management/audit",
    response_model=Page[AuditView],
    tags=["Audit"],
    operation_id="listRegistryAudit",
)
def list_audit(_operator: Operator, service: Service, limit: Limit = 50, cursor: Cursor = None):
    rows, next_cursor = service.page(RegistryAudit, limit, cursor)
    return {"items": rows, "next_cursor": next_cursor}


@router.get("/things", response_model=Page[DeviceView], tags=["Things"], operation_id="listThings")
def list_things(
    service: Service,
    limit: Limit = 50,
    cursor: Cursor = None,
    domain_ref: Annotated[str | None, Query(max_length=200)] = None,
    space_ref: Annotated[str | None, Query(max_length=200)] = None,
    capability: Annotated[str | None, Query(max_length=64)] = None,
):
    if domain_ref is not None and domain_ref != service.domain:
        raise APIError(403, "forbidden", "Domain is outside the granted scope")
    rows, next_cursor = service.page(
        Device, limit, cursor, space_ref=space_ref, capability=capability
    )
    return {
        "items": [device_view(service.session, row) for row in rows],
        "next_cursor": next_cursor,
    }


@router.get(
    "/things/{thing_id}",
    response_model=ThingView,
    response_model_exclude_none=True,
    tags=["Things"],
    operation_id="getThing",
)
def get_thing(thing_id: str, service: Service):
    device = service.get(Device, thing_id)
    model = service.get(DeviceModel, device.model_id)
    return ThingView(
        **device_view(service.session, device).model_dump(),
        properties=model.properties,
        actions=model.actions,
        events=model.events,
    )

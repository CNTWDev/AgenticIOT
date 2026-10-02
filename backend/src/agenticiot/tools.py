"""Thin typed HTTP tools over device services, not an Agent scheduler or MCP server."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from agenticiot.access.api import Database
from agenticiot.registry.models import DeviceModel
from agenticiot.registry.schemas import ResourceID, SchemaModel
from agenticiot.runtime.schemas import CommandView, InvokeRequest
from agenticiot.runtime.service import RuntimeService
from agenticiot.security import Authenticated

router = APIRouter(prefix="/v1/tools", tags=["Agent device tools"])


class ToolAction(SchemaModel):
    thing_id: ResourceID
    action: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    input: dict[str, Any]
    # Supplied by trusted deterministic runtime, never regenerated on a network retry.
    operation_id: str = Field(min_length=1, max_length=200)


@router.get("", operation_id="listAgentTools")
def catalog(principal: Authenticated):
    principal.require("device:read")
    action_schema = ToolAction.model_json_schema()
    action_schema["properties"].pop("operation_id")
    action_schema["required"].remove("operation_id")
    return {
        "protocol": "typed-http-tools-v1",
        "tools": [
            {"name": "discover_devices", "method": "GET", "path": "/v1/things"},
            {"name": "read_capabilities", "method": "GET", "path": "/v1/tools/devices/{id}/schema"},
            {"name": "read_state", "method": "GET", "path": "/v1/things/{id}/state"},
            {
                "name": "invoke_action",
                "method": "POST",
                "path": "/v1/tools/actions",
                "input_schema": action_schema,
                "runtime_injected_fields": ["operation_id"],
            },
            {"name": "read_command", "method": "GET", "path": "/v1/commands/{id}"},
        ],
        "agent_activation": "owned_by_entry_runtime",
    }


@router.get("/devices/{thing_id}/schema", operation_id="getDeviceToolSchema")
def schema(thing_id: str, request: Request, principal: Authenticated, session: Database):
    from agenticiot.registry.models import Device

    principal.require("device:read")
    service = RuntimeService(session, principal, request.state.trace_id)
    device = service.get(Device, thing_id)
    model = service.get(DeviceModel, device.model_id)
    return {
        "thing_id": thing_id,
        "model_version": model.version,
        "properties": model.properties,
        "actions": model.actions,
        "events": model.events,
        "execution_mode": "simulated_low_risk_absolute_set_only",
    }


@router.post(
    "/actions", status_code=202, operation_id="invokeDeviceTool", response_model=CommandView
)
def invoke(data: ToolAction, request: Request, principal: Authenticated, session: Database):
    principal.require("device:act")
    service = RuntimeService(session, principal, request.state.trace_id)
    command = service.invoke(
        data.thing_id, data.action, InvokeRequest(input=data.input), data.operation_id
    )
    request.app.state.node_hub.notify(service.binding(command.thing_id).edge_id)
    return command

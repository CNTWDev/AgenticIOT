import json
from datetime import datetime
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
ResourceID = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class SchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, from_attributes=True)


def validate_definition(value: dict[str, Any]) -> dict[str, Any]:
    """Inline, typed JSON Schema 2020-12; no reference resolution or network retrieval."""
    if len(json.dumps(value)) > 32768:
        raise ValueError("Each capability schema must be at most 32 KiB")

    def visit(node: Any, depth: int = 0):
        if depth > 16:
            raise ValueError("Capability schemas may nest at most 16 levels")
        if isinstance(node, dict):
            if any(key in node for key in ("$ref", "$dynamicRef", "$recursiveRef", "$id")):
                raise ValueError("Schema references and identifiers are not supported")
            for child in node.values():
                visit(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                visit(child, depth + 1)

    visit(value)
    if value.get("type") not in (
        "object",
        "array",
        "string",
        "integer",
        "number",
        "boolean",
        "null",
    ):
        raise ValueError("A capability schema must declare one explicit JSON type")
    if "$schema" in value and value["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("Only JSON Schema 2020-12 is supported")
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as exc:
        raise ValueError(f"Invalid JSON Schema: {exc.message}") from None
    return value


Definition = Annotated[dict[str, Any], Field(description="Inline JSON Schema 2020-12")]


class PropertySpec(SchemaModel):
    definition: Definition = Field(alias="schema")
    readable: bool = True
    writable: bool = False
    unit: str | None = Field(default=None, max_length=40)
    _validate_schema = field_validator("definition")(validate_definition)


class ActionSpec(SchemaModel):
    input_schema: Definition
    output_schema: Definition | None = None
    risk: Literal["low", "medium", "high", "critical"]
    confirmation: Literal["adapter_ack", "observed_state", "device_event", "none"]
    offline_policy: Literal[
        "local_always", "cached_authorization", "online_required", "local_only"
    ] = "online_required"

    @field_validator("input_schema", "output_schema")
    @classmethod
    def check_schema(cls, value):
        return validate_definition(value) if value is not None else None

    @model_validator(mode="after")
    def check_confirmation(self):
        if self.risk in ("high", "critical") and self.confirmation == "none":
            raise ValueError("High-risk actions require completion evidence")
        return self


class EventSpec(SchemaModel):
    data_schema: Definition
    _validate_schema = field_validator("data_schema")(validate_definition)


class Capabilities(SchemaModel):
    properties: dict[Name, PropertySpec] = Field(default_factory=dict, max_length=64)
    actions: dict[Name, ActionSpec] = Field(default_factory=dict, max_length=64)
    events: dict[Name, EventSpec] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def require_capability(self):
        if not (self.properties or self.actions or self.events):
            raise ValueError("At least one property, action or event is required")
        catalog = {
            kind: {
                name: spec.model_dump(by_alias=True) for name, spec in getattr(self, kind).items()
            }
            for kind in ("properties", "actions", "events")
        }
        if len(json.dumps(catalog)) > 262144:
            raise ValueError("A capability catalog must be at most 256 KiB")
        return self


class ModelCreate(Capabilities):
    key: Name
    version: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", max_length=40
    )
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


class ModelView(ModelCreate):
    id: ResourceID
    domain_ref: str
    created_at: datetime


class DeviceCreate(SchemaModel):
    model_id: ResourceID
    external_ref: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=160)
    space_ref: str | None = Field(default=None, min_length=1, max_length=200)


class DeviceView(DeviceCreate):
    id: ResourceID
    domain_ref: str
    model_version: str
    lifecycle_status: Literal["commissioning", "active", "suspended", "retiring", "retired"]
    reachability: Literal["online", "sleeping", "offline", "unknown"]
    revision: int
    created_at: datetime


class ThingView(DeviceView, Capabilities):
    pass


class DevicePatch(SchemaModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    space_ref: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set:
            raise ValueError("Provide a title or space_ref")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be null")
        return self


class AuditView(SchemaModel):
    id: ResourceID
    domain_ref: str
    subject_ref: str
    operation: str
    resource_id: str
    trace_id: str
    occurred_at: datetime


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None

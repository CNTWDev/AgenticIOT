from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictInt

from agenticiot.registry.schemas import ResourceID, SchemaModel

AdapterID = Literal["virtual-light-v1", "mqtt-light-demo-v1"]


class EdgeRegistration(SchemaModel):
    title: str = Field(min_length=1, max_length=160)
    version: Literal["0.1.0"] = "0.1.0"
    adapters: list[AdapterID] = Field(
        default_factory=lambda: ["virtual-light-v1"], min_length=1, max_length=2
    )


class EdgeView(EdgeRegistration):
    id: ResourceID
    edge_ref: str
    domain_ref: str
    last_seen_at: datetime


class BindingCreate(SchemaModel):
    edge_id: ResourceID
    adapter: AdapterID = "virtual-light-v1"


class BindingView(BindingCreate):
    id: ResourceID
    thing_id: ResourceID
    simulated: Literal[True] = True


class InvokeRequest(SchemaModel):
    input: dict[str, Any]
    deadline: AwareDatetime | None = None


class LightValues(SchemaModel):
    power: StrictBool
    brightness: Annotated[StrictInt, Field(ge=0, le=100)]


class ObservationInput(SchemaModel):
    source_sequence: int = Field(ge=1, le=2**63 - 1, strict=True)
    observed_at: AwareDatetime
    values: LightValues


class SuccessReport(ObservationInput):
    outcome: Literal["succeeded"]


class FailureReport(SchemaModel):
    outcome: Literal["failed"]
    error_code: Literal["adapter_failure", "deadline_elapsed", "execution_uncertain"]


ResultReport = Annotated[SuccessReport | FailureReport, Field(discriminator="outcome")]


class ReceiptView(SchemaModel):
    command_id: ResourceID
    sequence: int
    stage: str
    executor_ref: str | None
    occurred_at: datetime
    evidence: dict[str, Any]
    error_code: str | None
    trace_id: str


class CommandView(SchemaModel):
    id: ResourceID
    thing_id: ResourceID
    adapter: AdapterID
    action: str
    input: dict[str, Any]
    status: Literal["accepted", "running", "succeeded", "failed", "unknown", "expired"]
    created_at: datetime
    deadline: datetime
    trace_id: str
    receipts: list[ReceiptView]
    simulated: Literal[True] = True


class ObservedValue(SchemaModel):
    value: Any
    source_ref: str
    observed_at: datetime
    received_at: datetime
    source_version: str
    freshness: Literal["fresh", "aging", "stale"]
    quality: Literal["good"] = "good"


class ThingState(SchemaModel):
    thing_id: ResourceID
    properties: dict[str, ObservedValue]
    simulated: Literal[True] = True

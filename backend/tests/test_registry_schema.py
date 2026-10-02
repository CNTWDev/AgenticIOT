from copy import deepcopy

import pytest
from agenticiot.registry.schemas import ModelCreate
from pydantic import ValidationError


def light_definition():
    return {
        "key": "virtual_light",
        "version": "1.0.0",
        "title": "Virtual light",
        "properties": {"power": {"schema": {"type": "boolean"}, "readable": True}},
        "actions": {
            "set_power": {
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "boolean"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "risk": "low",
                "confirmation": "observed_state",
            }
        },
        "events": {"power_changed": {"data_schema": {"type": "boolean"}}},
    }


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "not-a-type"},
        {"type": "integer", "minimum": "zero"},
        {"type": "object", "$ref": "https://example.invalid/schema"},
        {"type": "object", "$ref": "#/$defs/example"},
        {"type": "object", "properties": {"child": {"$dynamicRef": "#node"}}},
        {"type": "string", "$schema": "http://json-schema.org/draft-07/schema#"},
        {"type": "string", "description": "x" * 32769},
    ],
)
def test_invalid_or_referencing_capability_schema_is_rejected(schema):
    data = light_definition()
    data["properties"]["power"]["schema"] = schema
    with pytest.raises(ValidationError):
        ModelCreate.model_validate(data)


def test_risk_confirmation_and_catalog_requirements():
    data = light_definition()
    data["actions"]["set_power"].update(risk="high", confirmation="none")
    with pytest.raises(ValidationError):
        ModelCreate.model_validate(data)
    with pytest.raises(ValidationError):
        ModelCreate(key="light", version="1.0.0", title="empty")


def test_published_catalog_preserves_exact_schema_and_defaults():
    source = light_definition()
    model = ModelCreate.model_validate(deepcopy(source))
    assert model.model_dump(by_alias=True)["properties"]["power"]["schema"] == {"type": "boolean"}
    assert model.actions["set_power"].offline_policy == "online_required"

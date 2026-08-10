"""JSON-schema generation must preserve modern Python type semantics."""

from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from tools.schema_utils import signature_to_json_schema


class _Record(BaseModel):
    value: int


def test_optional_and_generic_types_are_not_flattened_to_strings() -> None:
    def operation(name: str | None = None, values: list[int] | None = None) -> None:
        return None

    schema = signature_to_json_schema(operation)
    name_schema = schema["properties"]["name"]
    values_schema = schema["properties"]["values"]
    assert {item.get("type") for item in name_schema["anyOf"]} == {"string", "null"}
    arrays = [item for item in values_schema["anyOf"] if item.get("type") == "array"]
    assert arrays and arrays[0]["items"]["type"] == "integer"
    assert name_schema["default"] is None
    assert values_schema["default"] is None
    assert schema["additionalProperties"] is False
    assert "required" not in schema


def test_nested_model_defs_are_lifted_to_root() -> None:
    def operation(records: list[_Record]) -> None:
        return None

    schema = signature_to_json_schema(operation)
    assert "_Record" in schema["$defs"]
    item = schema["properties"]["records"]["items"]
    assert item["$ref"] == "#/$defs/_Record"


def test_annotated_constraints_are_preserved() -> None:
    def operation(count: Annotated[int, Field(gt=0, description="positive count")]) -> None:
        return None

    property_schema = signature_to_json_schema(operation)["properties"]["count"]
    assert property_schema["exclusiveMinimum"] == 0
    assert property_schema["description"] == "positive count"


def test_one_unresolved_forward_ref_does_not_flatten_other_parameters() -> None:
    def operation(record: _Record, missing: str) -> None:
        return None

    operation.__annotations__["missing"] = "MissingModel"
    schema = signature_to_json_schema(operation)
    record_schema = schema["properties"]["record"]
    assert record_schema["type"] == "object"
    assert record_schema["properties"]["value"]["type"] == "integer"
    assert schema["properties"]["missing"]["type"] == "string"


def test_unrelated_schema_generator_errors_are_not_silenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import schema_utils

    def explode(self):
        raise RuntimeError("schema implementation bug")

    monkeypatch.setattr(schema_utils.TypeAdapter, "json_schema", explode)

    def operation(value: int) -> None:
        return None

    with pytest.raises(RuntimeError, match="schema implementation bug"):
        signature_to_json_schema(operation)

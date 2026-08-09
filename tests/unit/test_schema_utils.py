"""JSON-schema generation must preserve modern Python type semantics."""

from tools.schema_utils import signature_to_json_schema


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

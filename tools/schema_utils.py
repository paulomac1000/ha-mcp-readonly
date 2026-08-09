"""Generate JSON Schema from application operation signatures."""

from __future__ import annotations

import inspect
from typing import Any, get_type_hints

from pydantic import TypeAdapter


def signature_to_json_schema(function: Any) -> dict[str, Any]:
    """Return a bounded object schema preserving unions, generics, and nullability."""
    signature = inspect.signature(function)
    try:
        type_hints = get_type_hints(function)
    except (NameError, TypeError):
        type_hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if name == "self":
            continue
        annotation = type_hints.get(name, parameter.annotation)
        if annotation is inspect.Parameter.empty:
            property_schema: dict[str, Any] = {}
        else:
            try:
                property_schema = TypeAdapter(annotation).json_schema()
            except Exception:
                property_schema = {"type": "string"}
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        else:
            property_schema = dict(property_schema)
            property_schema["default"] = parameter.default
        properties[name] = property_schema

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema

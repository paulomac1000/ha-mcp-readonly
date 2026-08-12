"""Application-owned JSON-schema helpers for callable signatures."""

from __future__ import annotations

import inspect
from typing import Any, get_type_hints

from pydantic import TypeAdapter
from pydantic.errors import PydanticSchemaGenerationError


def _resolved_annotations(function: Any) -> dict[str, Any]:
    """Resolve annotations wholesale when possible, otherwise retain raw values."""
    raw = inspect.get_annotations(function, eval_str=False)
    try:
        resolved = get_type_hints(function, include_extras=True)
    except (NameError, TypeError):
        return raw
    return {**raw, **resolved}


def _parameter_schema(annotation: Any, namespace: dict[str, Any]) -> dict[str, Any]:
    if annotation is inspect.Signature.empty:
        return {"type": "string"}
    try:
        adapter = TypeAdapter(annotation)
        if isinstance(annotation, str):
            rebuilt = adapter.rebuild(
                force=True,
                raise_errors=False,
                _types_namespace=namespace,
            )
            if rebuilt is False:
                return {"type": "string"}
        return adapter.json_schema()
    except PydanticSchemaGenerationError:
        return {"type": "string"}


def _merge_defs(root: dict[str, Any], parameter_schema: dict[str, Any]) -> dict[str, Any]:
    """Lift TypeAdapter definitions so local #/$defs references resolve from the root schema."""
    schema = dict(parameter_schema)
    definitions = schema.pop("$defs", None)
    if not isinstance(definitions, dict):
        return schema
    root_definitions = root.setdefault("$defs", {})
    for name, definition in definitions.items():
        existing = root_definitions.get(name)
        if existing is not None and existing != definition:
            raise ValueError(f"Conflicting JSON-schema definition: {name}")
        root_definitions[name] = definition
    return schema


def signature_to_json_schema(function: Any) -> dict[str, Any]:
    """Build a JSON schema while preserving unions, generics, refs, and Annotated metadata."""
    signature = inspect.signature(function)
    annotations = _resolved_annotations(function)
    namespace = dict(getattr(function, "__globals__", {}))
    schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
    required: list[str] = []

    for name, parameter in signature.parameters.items():
        if name == "self":
            continue
        annotation = annotations.get(name, parameter.annotation)
        property_schema = _merge_defs(schema, _parameter_schema(annotation, namespace))
        if parameter.default is inspect.Signature.empty:
            required.append(name)
        else:
            property_schema["default"] = parameter.default
        schema["properties"][name] = property_schema

    if required:
        schema["required"] = required
    return schema

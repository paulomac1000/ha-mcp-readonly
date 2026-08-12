"""Positive allowlist and typed sanitizers for model-visible Home Assistant .storage data."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

StorageSanitizer = Callable[[dict[str, Any]], dict[str, Any]]


def _wrapper(raw: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Preserve only non-secret registry envelope metadata."""
    out: dict[str, Any] = {}
    for key in ("version", "minor_version", "key"):
        value = raw.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
    out["data"] = data
    return out


def _filter_rows(raw: dict[str, Any], collection: str, fields: frozenset[str]) -> dict[str, Any]:
    data = raw.get("data")
    rows = data.get(collection) if isinstance(data, dict) else None
    safe_rows: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            safe_rows.append({key: row[key] for key in fields if key in row})
    return _wrapper(raw, {collection: safe_rows})


_ENTITY_FIELDS = frozenset(
    {
        "entity_id",
        "platform",
        "unique_id",
        "device_id",
        "area_id",
        "config_entry_id",
        "disabled_by",
        "hidden_by",
        "name",
        "original_name",
        "icon",
        "original_icon",
        "entity_category",
        "device_class",
        "original_device_class",
        "unit_of_measurement",
        "labels",
        "categories",
        "aliases",
        "translation_key",
        "has_entity_name",
    }
)
_DEVICE_FIELDS = frozenset(
    {
        "id",
        "config_entries",
        "connections",
        "identifiers",
        "manufacturer",
        "model",
        "model_id",
        "name",
        "name_by_user",
        "area_id",
        "disabled_by",
        "entry_type",
        "hw_version",
        "sw_version",
        "serial_number",
        "via_device_id",
        "labels",
    }
)
_AREA_FIELDS = frozenset({"id", "name", "picture", "aliases", "labels", "floor_id"})
_FLOOR_FIELDS = frozenset({"floor_id", "id", "name", "level", "icon", "aliases"})
_LABEL_FIELDS = frozenset({"label_id", "id", "name", "color", "icon", "description"})
_CATEGORY_FIELDS = frozenset({"category_id", "id", "name", "icon"})
_TAG_FIELDS = frozenset({"id", "name", "last_scanned"})


def _entity_registry(raw: dict[str, Any]) -> dict[str, Any]:
    return _filter_rows(raw, "entities", _ENTITY_FIELDS)


def _device_registry(raw: dict[str, Any]) -> dict[str, Any]:
    return _filter_rows(raw, "devices", _DEVICE_FIELDS)


def _area_registry(raw: dict[str, Any]) -> dict[str, Any]:
    return _filter_rows(raw, "areas", _AREA_FIELDS)


def _floor_registry(raw: dict[str, Any]) -> dict[str, Any]:
    return _filter_rows(raw, "floors", _FLOOR_FIELDS)


def _label_registry(raw: dict[str, Any]) -> dict[str, Any]:
    return _filter_rows(raw, "labels", _LABEL_FIELDS)


def _category_registry(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data")
    if not isinstance(data, dict):
        return _wrapper(raw, {"categories": []})
    categories = data.get("categories")
    safe: list[dict[str, Any]] = []
    if isinstance(categories, list):
        safe = [
            {key: row[key] for key in _CATEGORY_FIELDS if key in row}
            for row in categories
            if isinstance(row, dict)
        ]
    elif isinstance(categories, dict):
        safe = [
            {"scope": scope, **{key: value[key] for key in _CATEGORY_FIELDS if key in value}}
            for scope, value in categories.items()
            if isinstance(scope, str) and isinstance(value, dict)
        ]
    return _wrapper(raw, {"categories": safe})


def _tag_registry(raw: dict[str, Any]) -> dict[str, Any]:
    return _filter_rows(raw, "tags", _TAG_FIELDS)


def _config_entries(raw: dict[str, Any]) -> dict[str, Any]:
    """Expose integration identity/health, never arbitrary integration credentials."""
    data = raw.get("data")
    rows = data.get("entries") if isinstance(data, dict) else None
    safe_rows: list[dict[str, Any]] = []
    allowed = frozenset(
        {
            "entry_id",
            "domain",
            "title",
            "source",
            "state",
            "disabled_by",
            "pref_disable_new_entities",
            "pref_disable_polling",
            "version",
            "minor_version",
            "supports_options",
            "supports_remove_device",
        }
    )
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = {key: row[key] for key in allowed if key in row}
            # Zone config entries contain geospatial configuration rather than credentials.
            raw_data = row.get("data")
            safe_data: dict[str, Any] = {}
            if row.get("domain") == "zone" and isinstance(raw_data, dict):
                safe_data = {
                    key: raw_data[key]
                    for key in ("latitude", "longitude", "radius", "passive")
                    if key in raw_data
                }
            if isinstance(raw_data, dict):
                # Preserve the mapping shape expected by internal analyzers while
                # exposing only a typed safe projection to the model artifact.
                item["data"] = safe_data
                if set(raw_data) - set(safe_data):
                    item["data_redacted"] = True
            safe_rows.append(item)
    return _wrapper(raw, {"entries": safe_rows})


def _lovelace_resources(raw: dict[str, Any]) -> dict[str, Any]:
    """Expose resource type and a credential-free URL projection."""
    data = raw.get("data")
    rows = data.get("items") if isinstance(data, dict) else None
    if rows is None and isinstance(data, dict):
        rows = data.get("resources")
    safe_rows: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            item: dict[str, Any] = {}
            resource_type = row.get("res_type", row.get("type"))
            if isinstance(resource_type, str):
                item["type"] = resource_type
            url = row.get("url")
            if isinstance(url, str):
                parts = urlsplit(url)
                # Query strings/fragments are not needed for topology and may contain tokens.
                item["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            if item:
                safe_rows.append(item)
    return _wrapper(raw, {"items": safe_rows})


def _hacs_repositories(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data")
    repositories = data.get("repositories") if isinstance(data, dict) else None
    fields = frozenset(
        {
            "name",
            "full_name",
            "category",
            "installed_version",
            "available_version",
            "status",
            "repository_manifest",
        }
    )
    safe: list[dict[str, Any]] = []
    iterable: Any
    if isinstance(repositories, dict):
        iterable = repositories.values()
    else:
        iterable = repositories
    if isinstance(iterable, list) or isinstance(repositories, dict):
        for row in iterable:
            if isinstance(row, dict):
                safe.append({key: row[key] for key in fields if key in row})
    return _wrapper(raw, {"repositories": safe})


SAFE_STORAGE_SANITIZERS: dict[str, StorageSanitizer] = {
    "core.entity_registry": _entity_registry,
    "core.device_registry": _device_registry,
    "core.area_registry": _area_registry,
    "core.floor_registry": _floor_registry,
    "core.label_registry": _label_registry,
    "core.category_registry": _category_registry,
    "core.config_entries": _config_entries,
    "core.tag": _tag_registry,
    "lovelace.resources": _lovelace_resources,
    "hacs.repositories": _hacs_repositories,
}


def sanitize_model_visible_storage(name: str, raw: Any) -> dict[str, Any] | None:
    """Return a typed safe projection, or None when a .storage schema is not allowlisted."""
    if not isinstance(raw, dict):
        return None
    sanitizer = SAFE_STORAGE_SANITIZERS.get(name)
    if sanitizer is None:
        return None
    return sanitizer(raw)

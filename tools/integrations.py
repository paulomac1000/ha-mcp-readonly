"""Integration Analysis Tools (P3 - Nice to have).

Provides tools for integration analysis:
- get_integration_entities(domain)
- get_integration_summary(domain)
"""

from collections import Counter
from typing import Any

from tools.utils import (
    _error_response,
    _success_response,
    get_best_name,
    get_registry_config_entries,
    get_registry_devices,
    get_registry_entities,
    make_ha_request,
)

TOOLS_VERSION = "1.0.0"


def _get_entries_by_domain(entries: list[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
    """Get all config entries for a domain."""
    return [entry for entry in entries if entry.get("domain") == domain]


def _get_entities_for_domain(entities: list[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
    """Get all entities for a domain based on platform."""
    domain_entities = []
    for entity in entities:
        if entity.get("platform") == domain:
            domain_entities.append(entity)
    return domain_entities


# =========================================================================
# _do_* FUNCTIONS (pure business logic, returns dict)
# =========================================================================


def _do_get_integration_entities(
    domain: str,
    include_disabled: bool,
    include_options: bool,
    config_path: str,
    ha_url: str | None,
    ha_token: str | None,
) -> dict[str, Any]:
    """Business logic for get_integration_entities."""
    entities = _get_entities_for_domain(get_registry_entities(config_path), domain)

    if not entities:
        return {"error": f"No entities found for integration '{domain}'"}

    devices = get_registry_devices(config_path)
    devices_map = {d["id"]: d for d in devices}

    states_map = {}
    if ha_url and ha_token:
        states_result = make_ha_request(ha_url, ha_token, "/api/states")
        if states_result.get("success"):
            states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

    by_device = {}
    disabled_count = 0
    unavailable_count = 0

    by_device["no_device"] = {"device_name": "No Device Assigned", "entities": []}

    for entity in entities:
        if entity.get("disabled_by"):
            disabled_count += 1
            if not include_disabled:
                continue

        eid = entity.get("entity_id")
        state = states_map.get(eid, {}).get("state", "unknown")
        if state == "unavailable" and not entity.get("disabled_by"):
            unavailable_count += 1

        entity_info = {
            "entity_id": eid,
            "name": get_best_name(entity, "entity"),
            "state": state,
            "disabled_by": entity.get("disabled_by"),
        }

        device_id = entity.get("device_id")
        if device_id and device_id in devices_map:
            if device_id not in by_device:
                device = devices_map[device_id]
                by_device[device_id] = {
                    "device_name": get_best_name(device, "device"),
                    "model": device.get("model"),  # type: ignore[dict-item]
                    "entities": [],
                }
            by_device[device_id]["entities"].append(entity_info)  # type: ignore[attr-defined]
        else:
            by_device["no_device"]["entities"].append(entity_info)  # type: ignore[attr-defined]

    if not by_device["no_device"]["entities"]:
        del by_device["no_device"]

    returned_count = sum(len(d["entities"]) for d in by_device.values())

    result: dict[str, Any] = {
        "domain": domain,
        "total_entities": len(entities),
        "returned_entities": returned_count,
        "disabled_count": disabled_count,
        "unavailable_count": unavailable_count,
        "by_device": by_device,
    }

    if include_options:
        entries = _get_entries_by_domain(get_registry_config_entries(config_path), domain)
        options_list = []
        for entry in entries:
            options_list.append(
                {
                    "entry_id": entry.get("entry_id"),
                    "title": entry.get("title"),
                    "options": entry.get("options", {}),
                }
            )
        result["config_entries_options"] = options_list

    return result


def _do_get_integration_summary(
    domain: str,
    config_path: str,
    ha_url: str | None,
    ha_token: str | None,
) -> dict[str, Any]:
    """Business logic for get_integration_summary."""
    entries = _get_entries_by_domain(get_registry_config_entries(config_path), domain)
    entities = _get_entities_for_domain(get_registry_entities(config_path), domain)

    if not entries and not entities:
        return {"error": f"Integration '{domain}' not found"}

    entries_summary = {
        "total": len(entries),
        "loaded": 0,
        "disabled": 0,
        "titles": [e.get("title") for e in entries[:5]],
    }

    for entry in entries:
        if entry.get("disabled_by"):
            entries_summary["disabled"] += 1  # type: ignore[operator]
        else:
            entries_summary["loaded"] += 1  # type: ignore[operator]

    states_map = {}
    if ha_url and ha_token:
        states_result = make_ha_request(ha_url, ha_token, "/api/states")
        if states_result.get("success"):
            states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

    entities_summary = {
        "total": len(entities),
        "enabled": 0,
        "disabled": 0,
        "available": 0,
        "unavailable": 0,
    }

    entity_platforms: Counter[str] = Counter()

    for entity in entities:
        eid = entity.get("entity_id", "unknown.unknown")
        platform = eid.split(".")[0]
        entity_platforms[platform] += 1

        if entity.get("disabled_by"):
            entities_summary["disabled"] += 1
        else:
            entities_summary["enabled"] += 1
            state = states_map.get(eid, {}).get("state", "unknown")
            if state in ["unavailable", "unknown"]:
                entities_summary["unavailable"] += 1
            else:
                entities_summary["available"] += 1

    devices = get_registry_devices(config_path)
    entry_ids = {e["entry_id"] for e in entries}
    domain_devices = [
        d for d in devices if any(entry_id in entry_ids for entry_id in d.get("config_entries", []))
    ]

    health = "Healthy" if entities_summary["unavailable"] == 0 else "Issues Detected"

    return {
        "domain": domain,
        "config_entries": entries_summary,
        "devices_count": len(domain_devices),
        "entities_summary": entities_summary,
        "entity_platforms": dict(entity_platforms),
        "health": health,
    }


# =========================================================================
# TOOL REGISTRATION
# =========================================================================


def register_integration_tools(mcp, config_path: str, ha_url: str, ha_token: str) -> None:  # type: ignore[no-untyped-def]
    """Register integration analysis tools."""

    @mcp.tool(name="get_integration_entities")
    async def get_integration_entities(
        domain: str, include_disabled: bool = False, include_options: bool = False
    ) -> str:
        """[READ] Get all entities for a given integration domain.

        Args:
            domain: Integration domain (e.g., "mqtt", "hue").
            include_disabled: Whether to include disabled entities.
            include_options: Whether to include config entry options (default: False).

        Returns:
            JSON string with domain summary, devices grouping, and availability stats.
        """
        try:
            result = _do_get_integration_entities(
                domain=domain,
                include_disabled=include_disabled,
                include_options=include_options,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool(name="get_integration_summary")
    async def get_integration_summary(domain: str) -> str:
        """[READ] Summarize an integration (devices, entities, health).

        Args:
            domain: Integration domain.

        Returns:
            JSON string with integration summary (entries, devices, entities, health).
        """
        try:
            result = _do_get_integration_summary(
                domain=domain,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

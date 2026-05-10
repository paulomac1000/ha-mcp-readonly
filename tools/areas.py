"""
Area Analysis Tools (P2 - UX)

Provides tools for area/room analysis:
- get_area_devices_summary(area_id)
"""

import logging

from tools.utils import (
    _error_response,
    _success_response,
    get_best_name,
    get_registry_areas,
    get_registry_config_entries,
    get_registry_devices,
    get_registry_entities,
    make_ha_request,
)

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


def _get_area_by_id_or_name(area_id: str, config_path: str) -> dict | None:
    """Find area by id or name (case-insensitive)."""
    areas = get_registry_areas(config_path)
    for area in areas:
        if area.get("id") == area_id:
            return area
        if area.get("name", "").lower() == area_id.lower():
            return area
    return None


def _get_integration_info(entry_id: str, config_path: str) -> str:
    """Get integration domain from config entry id."""
    entries = get_registry_config_entries(config_path)
    for entry in entries:
        if entry.get("entry_id") == entry_id:
            return entry.get("domain", "unknown")
    return "unknown"


def _do_get_area_devices_summary(area_id: str, ha_url: str, ha_token: str, config_path: str) -> str:
    """Summary of devices in an area."""
    area = _get_area_by_id_or_name(area_id, config_path)

    if not area:
        areas = get_registry_areas(config_path)
        available = ", ".join(a["name"] for a in areas[:10])
        return _error_response(f"Area '{area_id}' not found. Available areas: {available}")

    final_area_id = area.get("id")

    all_devices = get_registry_devices(config_path)
    area_devices = [d for d in all_devices if d.get("area_id") == final_area_id]
    all_entities = get_registry_entities(config_path)

    states_map = {}
    states_result = make_ha_request(ha_url, ha_token, "/api/states")
    if states_result.get("success"):
        states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

    devices_summary = []
    integrations_used = set()
    total_entities_count = 0
    unavailable_entities_count = 0

    for device in area_devices:
        device_id = device.get("id")
        device_entities = [e for e in all_entities if e.get("device_id") == device_id]
        entities_count = len(device_entities)
        total_entities_count += entities_count

        device_unavailable = 0
        for e in device_entities:
            eid = e.get("entity_id")
            state = states_map.get(eid, {}).get("state", "unknown")
            if state in ["unavailable", "unknown"]:
                device_unavailable += 1
        unavailable_entities_count += device_unavailable

        integration = "unknown"
        config_entries = device.get("config_entries", [])
        if config_entries:
            primary = device.get("primary_config_entry")
            entry_id = primary if primary else config_entries[0]
            integration = _get_integration_info(entry_id, config_path)
            integrations_used.add(integration)

        issues = []
        if device.get("disabled_by"):
            issues.append(f"Device disabled by {device.get('disabled_by')}")
        if entities_count > 0 and device_unavailable == entities_count:
            issues.append("All entities unavailable")
        elif device_unavailable > 0:
            issues.append(f"{device_unavailable}/{entities_count} entities unavailable")

        devices_summary.append(
            {
                "device_id": device_id,
                "name": get_best_name(device, "device"),
                "manufacturer": device.get("manufacturer"),
                "model": device.get("model"),
                "integration": integration,
                "entities_count": entities_count,
                "entities_available": entities_count - device_unavailable,
                "issues": issues,
            }
        )

    orphan_entities = [
        e for e in all_entities if e.get("area_id") == final_area_id and not e.get("device_id")
    ]

    if orphan_entities:
        orphan_unavailable = 0
        for e in orphan_entities:
            eid = e.get("entity_id")
            state = states_map.get(eid, {}).get("state", "unknown")
            if state in ["unavailable", "unknown"]:
                orphan_unavailable += 1
        total_entities_count += len(orphan_entities)
        unavailable_entities_count += orphan_unavailable
        devices_summary.append(
            {
                "device_id": None,
                "name": "Orphan Entities (No Device)",
                "manufacturer": "Home Assistant",
                "integration": "various",
                "entities_count": len(orphan_entities),
                "entities_available": len(orphan_entities) - orphan_unavailable,
                "issues": ["Entities assigned directly to area"]
                if len(orphan_entities) > 0
                else [],
            }
        )

    devices_summary.sort(key=lambda x: x["name"].lower())

    return _success_response(
        {
            "area_id": final_area_id,
            "area_name": area.get("name"),
            "devices": devices_summary,
            "integrations_used": sorted(list(integrations_used)),
            "total_entities": total_entities_count,
            "unavailable_entities": unavailable_entities_count,
        }
    )


def register_area_tools(mcp, config_path: str, ha_url: str, ha_token: str):
    """Register area analysis tools."""

    @mcp.tool()
    async def get_area_devices_summary(area_id: str) -> str:
        """[READ] Summary of devices in an area.

        ~80% token savings vs get_area_overview() + get_device_registry().

        Args:
            area_id: Area id or name (e.g. "bedroom", "Bedroom")

        Returns:
            JSON with:
            - area_id, area_name
            - devices[]: {device_id, name, manufacturer, integration,
                         config_entry_state, entities_count, issues[]}
            - integrations_used[]
            - total_entities, unavailable_entities
        """
        try:
            return _do_get_area_devices_summary(area_id, ha_url, ha_token, config_path)
        except Exception as e:
            return _error_response(str(e))

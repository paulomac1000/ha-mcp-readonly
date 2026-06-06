"""
Device Tools (P0 - Critical)

Provides tools for device management and context:
- get_device_details(device_id)
- get_device_entities(device_id)
- search_devices(search_term, manufacturer, area_id)
"""

import logging
from typing import Any

from tools.manifests import make_manifest, register_manifest
from tools.utils import (
    _error_response,
    _success_response,
    get_registry_areas,
    get_registry_config_entries,
    get_registry_devices,
    get_registry_entities,
    make_ha_request,
)

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


# =============================================================================
# HELPERS
# =============================================================================


def _get_device_by_id(device_id: str, config_path: str) -> dict | None:  # type: ignore[type-arg]
    """Find device by id in the device registry."""
    devices = get_registry_devices(config_path)
    for device in devices:
        if device.get("id") == device_id:
            return device
    return None


def _get_config_entry_info(entry_id: str, config_path: str) -> dict[str, Any]:
    """Get basic config entry info."""
    entries = get_registry_config_entries(config_path)
    for entry in entries:
        if entry.get("entry_id") == entry_id:
            return {
                "entry_id": entry_id,
                "domain": entry.get("domain"),
                "title": entry.get("title"),
                "disabled_by": entry.get("disabled_by"),
            }
    return {"entry_id": entry_id, "error": "not found"}


def _get_area_name(area_id: str | None, config_path: str) -> str | None:
    """Get area name by id."""
    if not area_id:
        return None
    areas = get_registry_areas(config_path)
    for area in areas:
        if area.get("id") == area_id:
            return area.get("name")
    return area_id


# =============================================================================
# DO FUNCTIONS
# =============================================================================


def _do_get_device_details(
    device_id: str, config_path: str, ha_url: str, ha_token: str, include_entities: bool = True
) -> str:
    """Fetch device details with full context."""
    device = _get_device_by_id(device_id, config_path)

    if not device:
        return _error_response(
            f"Device '{device_id}' not found. Use search_devices() to find valid device_id"
        )

    all_entities = get_registry_entities(config_path)
    device_entities = [e for e in all_entities if e.get("device_id") == device_id]

    states_map = {}
    states_result = make_ha_request(ha_url, ha_token, "/api/states")
    if states_result.get("success"):
        states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

    entities_list = []
    entities_summary = {
        "total": len(device_entities),
        "enabled": 0,
        "disabled": 0,
        "available": 0,
        "unavailable": 0,
    }

    for entity in device_entities:
        eid = entity.get("entity_id")
        disabled_by = entity.get("disabled_by")

        if disabled_by:
            entities_summary["disabled"] += 1
            state = "disabled"
        else:
            entities_summary["enabled"] += 1
            state_data = states_map.get(eid, {})
            state = state_data.get("state", "unknown")
            if state == "unavailable":
                entities_summary["unavailable"] += 1
            else:
                entities_summary["available"] += 1

        entities_list.append(
            {
                "entity_id": eid,
                "platform": entity.get("platform"),
                "original_name": entity.get("original_name"),
                "state": state,
                "disabled_by": disabled_by,
                "entity_category": entity.get("entity_category"),
                "device_class": entity.get("device_class"),
            }
        )

    entities_list.sort(key=lambda x: (x.get("disabled_by") is not None, x.get("entity_id", "")))

    config_entries_info = []
    for entry_id in device.get("config_entries", []):
        config_entries_info.append(_get_config_entry_info(entry_id, config_path))

    via_device_info = None
    if device.get("via_device_id"):
        via_device = _get_device_by_id(device.get("via_device_id"), config_path)  # type: ignore[arg-type]
        if via_device:
            via_device_info = {
                "device_id": via_device.get("id"),
                "name": via_device.get("name_by_user") or via_device.get("name"),
                "manufacturer": via_device.get("manufacturer"),
                "model": via_device.get("model"),
            }

    result = {
        "device_id": device_id,
        "name": device.get("name"),
        "name_by_user": device.get("name_by_user"),
        "display_name": device.get("name_by_user") or device.get("name"),
        "manufacturer": device.get("manufacturer"),
        "model": device.get("model"),
        "model_id": device.get("model_id"),
        "hw_version": device.get("hw_version"),
        "sw_version": device.get("sw_version"),
        "serial_number": device.get("serial_number"),
        "area_id": device.get("area_id"),
        "area_name": _get_area_name(device.get("area_id"), config_path),
        "disabled_by": device.get("disabled_by"),
        "entry_type": device.get("entry_type"),
        "config_entries": config_entries_info,
        "primary_config_entry": device.get("primary_config_entry"),
        "via_device": via_device_info,
        "connections": device.get("connections", []),
        "identifiers": device.get("identifiers", []),
        "labels": device.get("labels", []),
        "created_at": device.get("created_at"),
        "modified_at": device.get("modified_at"),
        "entities_summary": entities_summary,
    }

    if include_entities:
        result["entities"] = entities_list[:30]
        if len(entities_list) > 30:
            result["entities_note"] = f"Showing 30 of {len(entities_list)} entities"

    return _success_response(result)


def _do_get_device_entities(
    device_id: str,
    include_disabled: bool,
    include_states: bool,
    config_path: str,
    ha_url: str,
    ha_token: str,
) -> str:
    """Fetch list of entities belonging to a device."""
    device = _get_device_by_id(device_id, config_path)

    if not device:
        return _error_response(f"Device '{device_id}' not found")

    all_entities = get_registry_entities(config_path)
    device_entities = [e for e in all_entities if e.get("device_id") == device_id]

    if not include_disabled:
        device_entities = [e for e in device_entities if not e.get("disabled_by")]

    states_map = {}
    if include_states:
        states_result = make_ha_request(ha_url, ha_token, "/api/states")
        if states_result.get("success"):
            states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

    entities_list = []
    for entity in device_entities:
        eid = entity.get("entity_id")
        entity_info = {
            "entity_id": eid,
            "platform": entity.get("platform"),
            "original_name": entity.get("original_name"),
            "name": entity.get("name"),
            "entity_category": entity.get("entity_category"),
            "device_class": entity.get("device_class"),
            "disabled_by": entity.get("disabled_by"),
            "hidden_by": entity.get("hidden_by"),
        }
        if include_states and eid in states_map:
            state_data = states_map[eid]
            entity_info["state"] = state_data.get("state")
            entity_info["last_changed"] = state_data.get("last_changed")
            entity_info["last_updated"] = state_data.get("last_updated")
            attrs = state_data.get("attributes", {})
            for key in ["unit_of_measurement", "friendly_name", "device_class"]:
                if key in attrs:
                    entity_info[f"attr_{key}"] = attrs[key]
        entities_list.append(entity_info)

    entities_list.sort(key=lambda x: x.get("entity_id", ""))  # type: ignore[arg-type, return-value]

    return _success_response(
        {
            "device_id": device_id,
            "device_name": device.get("name_by_user") or device.get("name"),
            "total_entities": len(entities_list),
            "entities": entities_list,
        }
    )


def _do_search_devices(
    search_term: str | None,
    manufacturer: str | None,
    model: str | None,
    area_id: str | None,
    domain: str | None,
    disabled_only: bool,
    with_entities_count: bool,
    config_path: str,
) -> str:
    """Searches devices with filtering."""
    devices = get_registry_devices(config_path)
    all_entities = get_registry_entities(config_path) if with_entities_count else []
    config_entries = get_registry_config_entries(config_path) if domain else []

    entry_domain_map = {}
    if domain:
        for entry in config_entries:
            if entry.get("domain") == domain.lower():
                entry_domain_map[entry.get("entry_id")] = entry.get("domain")

    results = []

    for device in devices:
        if disabled_only and not device.get("disabled_by"):
            continue

        if search_term:
            term = search_term.lower()
            name = (device.get("name_by_user") or device.get("name") or "").lower()
            mfr = (device.get("manufacturer") or "").lower()
            mdl = (device.get("model") or "").lower()
            if term not in name and term not in mfr and term not in mdl:
                continue

        if manufacturer:
            if manufacturer.lower() not in (device.get("manufacturer") or "").lower():
                continue

        if model:
            if model.lower() not in (device.get("model") or "").lower():
                continue

        if area_id and device.get("area_id") != area_id:
            continue

        if domain:
            device_entries = device.get("config_entries", [])
            if not any(eid in entry_domain_map for eid in device_entries):
                continue

        result_device = {
            "device_id": device.get("id"),
            "name": device.get("name_by_user") or device.get("name"),
            "manufacturer": device.get("manufacturer"),
            "model": device.get("model"),
            "area_id": device.get("area_id"),
            "area_name": _get_area_name(device.get("area_id"), config_path),
            "disabled_by": device.get("disabled_by"),
            "via_device_id": device.get("via_device_id"),
        }

        if with_entities_count:
            device_entities = [e for e in all_entities if e.get("device_id") == device.get("id")]
            result_device["entities_count"] = len(device_entities)
            result_device["entities_disabled"] = sum(
                1 for e in device_entities if e.get("disabled_by")
            )

        results.append(result_device)

    results.sort(key=lambda x: x.get("name", "").lower())  # type: ignore[union-attr]

    return _success_response(
        {
            "filters": {
                "search_term": search_term,
                "manufacturer": manufacturer,
                "model": model,
                "area_id": area_id,
                "domain": domain,
                "disabled_only": disabled_only,
            },
            "total_devices": len(devices),
            "matched_count": len(results),
            "devices": results[:50],
        }
    )


def _do_get_devices_by_area(area_id: str, config_path: str) -> str:
    """Fetch all devices in a given area."""
    areas = get_registry_areas(config_path)
    area = next(
        (
            a
            for a in areas
            if a.get("id") == area_id or a.get("name", "").lower() == area_id.lower()
        ),
        None,
    )

    if not area:
        available = ", ".join(f"{a['name']} ({a['id']})" for a in areas[:10])
        return _error_response(f"Area '{area_id}' not found. Available areas: {available}")

    final_area_id = area.get("id")
    devices = get_registry_devices(config_path)
    area_devices = [d for d in devices if d.get("area_id") == final_area_id]

    all_entities = get_registry_entities(config_path)

    results = []
    for device in area_devices:
        device_entities = [e for e in all_entities if e.get("device_id") == device.get("id")]
        results.append(
            {
                "device_id": device.get("id"),
                "name": device.get("name_by_user") or device.get("name"),
                "manufacturer": device.get("manufacturer"),
                "model": device.get("model"),
                "disabled_by": device.get("disabled_by"),
                "entities_count": len(device_entities),
                "entities_disabled": sum(1 for e in device_entities if e.get("disabled_by")),
            }
        )

    results.sort(key=lambda x: x.get("name", "").lower())  # type: ignore[union-attr]

    return _success_response(
        {
            "area": {
                "id": area.get("id"),
                "name": area.get("name"),
                "aliases": area.get("aliases", []),
            },
            "devices_count": len(results),
            "devices": results,
        }
    )


def _do_device_get_wifi_status(device_id: str, config_path: str, ha_url: str, ha_token: str) -> str:
    """Get WiFi status for Tasmota/OpenBK devices."""
    device = _get_device_by_id(device_id, config_path)

    if not device:
        return _error_response(
            f"Device '{device_id}' not found. Use search_devices() to find valid device_id"
        )

    all_entities = get_registry_entities(config_path)
    device_entities = [e for e in all_entities if e.get("device_id") == device_id]

    wifi_data = {
        "device_id": device_id,
        "device_name": device.get("name_by_user") or device.get("name"),
        "manufacturer": device.get("manufacturer"),
        "model": device.get("model"),
        "connection_state": "unknown",
        "ssid": None,
        "rssi": None,
        "signal_quality": None,
        "ip_address": None,
        "mac_address": None,
        "uptime": None,
        "source": "none",
    }

    wifi_related_ids = ["rssi", "wifi", "signal", "ssid", "ip", "uptime", "status"]

    for entity in device_entities:
        entity_id = entity.get("entity_id", "").lower()

        if any(wifi_id in entity_id for wifi_id in wifi_related_ids):
            state_response = make_ha_request(
                ha_url, ha_token, f"/api/states/{entity.get('entity_id')}"
            )

            if state_response.get("success"):
                state_data = state_response.get("data", {})
                state = state_data.get("state")
                attrs = state_data.get("attributes", {})

                if "rssi" in entity_id and state and state != "unavailable":
                    try:
                        wifi_data["rssi"] = int(float(state))
                        rssi = wifi_data["rssi"]
                        quality = max(0, min(100, 2 * (rssi + 90)))  # type: ignore[operator]
                        wifi_data["signal_quality"] = round(quality)
                        wifi_data["source"] = "ha_sensor"
                    except (ValueError, TypeError):
                        pass

                elif "ssid" in entity_id and state:
                    wifi_data["ssid"] = state
                    wifi_data["source"] = "ha_sensor"

                elif "ip" in entity_id and state:
                    wifi_data["ip_address"] = state
                    wifi_data["source"] = "ha_sensor"

                elif "uptime" in entity_id and state:
                    wifi_data["uptime"] = state
                    wifi_data["source"] = "ha_sensor"

                device_class = attrs.get("device_class")
                if device_class == "signal_strength" and state and not wifi_data["rssi"]:
                    try:
                        wifi_data["rssi"] = int(float(state))
                    except (ValueError, TypeError):
                        pass

    if wifi_data["rssi"] is not None:
        wifi_data["connection_state"] = "connected" if wifi_data["rssi"] > -80 else "weak"  # type: ignore[operator]
    elif any(v is not None for v in [wifi_data["ssid"], wifi_data["ip_address"]]):
        wifi_data["connection_state"] = "connected"

    connections = device.get("connections", [])
    for conn in connections:
        if isinstance(conn, (list, tuple)) and len(conn) >= 2:
            if conn[0] == "mac":
                wifi_data["mac_address"] = conn[1]
                break

    return _success_response({"wifi_status": wifi_data})


def _do_get_device_triggers(
    device_id: str | None,
    entity_id: str | None,
    config_path: str,
    ha_url: str,
    ha_token: str,
) -> str:
    """Retrieve available device triggers for a device.

    Args:
        device_id: Device ID to look up triggers for.
        entity_id: Entity ID to resolve to device_id if device_id not provided.
        config_path: Path to HA config directory.
        ha_url: Home Assistant API URL.
        ha_token: Authorization token.

    Returns:
        JSON with success, device_id, and triggers list.
    """
    if not device_id and not entity_id:
        return _error_response("At least one of device_id or entity_id is required")

    resolved_device_id = device_id

    if not resolved_device_id and entity_id:
        all_entities = get_registry_entities(config_path)
        for ent in all_entities:
            if ent.get("entity_id") == entity_id:
                resolved_device_id = ent.get("device_id")
                break

    if not resolved_device_id:
        return _error_response(
            f"Could not resolve device_id from '{device_id or entity_id}'. "
            "Use search_devices() to find valid device IDs."
        )

    device = _get_device_by_id(resolved_device_id, config_path)
    if not device:
        return _error_response(
            f"Device '{resolved_device_id}' not found. Use search_devices() to find valid device_id"
        )

    all_entities = get_registry_entities(config_path)
    device_entities = [e for e in all_entities if e.get("device_id") == resolved_device_id]

    triggers: list[dict[str, Any]] = []

    triggerable_domains = {
        "binary_sensor": {"subtype": "state", "template": "{name} changed"},
        "sensor": {"subtype": "numeric_state", "template": "{name} value change"},
        "button": {"subtype": "pressed", "template": "{name} pressed"},
        "event": {"subtype": "event", "template": "{name} event"},
        "remote": {"subtype": "remote", "template": "{name} command"},
        "device_tracker": {"subtype": "zone", "template": "{name} zone change"},
        "update": {"subtype": "update", "template": "{name} update available"},
    }

    for entity in device_entities:
        eid = entity.get("entity_id", "")
        domain = eid.split(".")[0] if "." in eid else ""
        name = entity.get("original_name") or entity.get("name") or eid

        if domain in triggerable_domains:
            info = triggerable_domains[domain]
            triggers.append(
                {
                    "type": f"device.{domain}",
                    "subtype": info["subtype"],
                    "name": info["template"].format(name=name),
                    "entity_id": eid,
                }
            )

    for entry_id in device.get("config_entries", []):
        config_entries = get_registry_config_entries(config_path)
        for ce in config_entries:
            if ce.get("entry_id") == entry_id:
                domain = ce.get("domain", "")
                if domain == "mqtt" or domain == "zigbee2mqtt":
                    triggers.append(
                        {
                            "type": f"device.{domain}",
                            "subtype": "mqtt_discovery",
                            "name": f"MQTT device trigger ({domain})",
                            "entity_id": None,
                        }
                    )
                break

    return _success_response(
        {
            "device_id": resolved_device_id,
            "device_name": device.get("name_by_user") or device.get("name"),
            "manufacturer": device.get("manufacturer"),
            "model": device.get("model"),
            "triggers": triggers,
            "total_triggers": len(triggers),
        }
    )


# =============================================================================
# REGISTRATION
# =============================================================================


def register_device_tools(mcp, config_path: str, ha_url: str, ha_token: str) -> None:  # type: ignore[no-untyped-def]
    """Register device management tools."""

    @mcp.tool()
    async def get_device_details(
        device_id: str, include_entities: bool = True
    ) -> str:
        """[READ] Fetches device details with full context.

        ~90% token savings vs get_device_registry() + filtering.

        Args:
            device_id: Device id (e.g. "c67a8024bc53a3d38dacc8c8c6e01cf6")
            include_entities: Whether to include entity list (default: True).

        Returns:
            JSON with:
            - device_id, name, name_by_user
            - manufacturer, model, model_id, hw_version, sw_version
            - area_id, area_name
            - config_entries[]: related config entries with their state
            - primary_config_entry
            - disabled_by
            - via_device: if device is via a hub
            - connections, identifiers
            - entities[]: list of entities with their states
            - entities_summary: {total, available, unavailable, disabled}
        """
        try:
            return _do_get_device_details(device_id, config_path, ha_url, ha_token, include_entities)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_device_entities(
        device_id: str, include_disabled: bool = False, include_states: bool = True
    ) -> str:
        """[READ] Fetches list of entities belonging to a device.

        Args:
            device_id: Device id
            include_disabled: Whether to include disabled entities (default: False)
            include_states: Whether to include current states (default: True)

        Returns:
            JSON with list of entities and their states
        """
        try:
            return _do_get_device_entities(
                device_id, include_disabled, include_states, config_path, ha_url, ha_token
            )
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def search_devices(
        search_term: str | None = None,
        manufacturer: str | None = None,
        model: str | None = None,
        area_id: str | None = None,
        domain: str | None = None,
        disabled_only: bool = False,
        with_entities_count: bool = False,
    ) -> str:
        """[READ] Searches devices with filtering.

        Args:
            search_term: Searches in name, manufacturer, model (case-insensitive)
            manufacturer: Filter by manufacturer
            model: Filter by model
            area_id: Filter by area
            domain: Filter by integration domain (e.g. "mqtt", "zha")
            disabled_only: Only disabled devices
            with_entities_count: Include entity count (slower)

        Returns:
            JSON with list of matching devices

        Examples:
            search_devices(search_term="sonoff")
            search_devices(manufacturer="acme_sensors", area_id="living_room")
            search_devices(domain="mqtt", with_entities_count=True)
        """
        try:
            return _do_search_devices(
                search_term,
                manufacturer,
                model,
                area_id,
                domain,
                disabled_only,
                with_entities_count,
                config_path,
            )
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_devices_by_area(area_id: str) -> str:
        """[READ] Fetches all devices in a given area.

        Args:
            area_id: Area id (e.g. "living_room", "office")

        Returns:
            JSON with list of devices in area
        """
        try:
            return _do_get_devices_by_area(area_id, config_path)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def device_get_wifi_status(device_id: str) -> str:
        """[READ] Get WiFi status for Tasmota/OpenBK devices.

        Queries the device directly via HTTP if available, or returns
        cached status from Home Assistant sensors.

        Args:
            device_id: id of the Tasmota/OpenBK device

        Returns:
            JSON with WiFi status including:
            - connection_state: connected/disconnected
            - ssid: network name
            - rssi: signal strength in dBm
            - signal_quality: percentage (0-100)
            - ip_address: device IP
            - mac_address: device MAC
            - uptime: device uptime
            - source: how the data was obtained (direct/http/ha_sensor)
        """
        try:
            return _do_device_get_wifi_status(device_id, config_path, ha_url, ha_token)
        except Exception as e:
            return _error_response(str(e))

    register_manifest(
        "get_device_triggers",
        make_manifest("get_device_triggers", latency="moderate"),
    )

    @mcp.tool()
    async def get_device_triggers(
        device_id: str | None = None, entity_id: str | None = None
    ) -> str:
        """[READ] Retrieve available device triggers for a device.

        Args:
            device_id: Device ID to look up triggers for.
            entity_id: Entity ID to resolve to device_id (e.g. "light.living_room").

        Returns:
            JSON with device info and triggers list (type, subtype, name).
            Returns empty list if no triggers found (success remains true).
        """
        try:
            return _do_get_device_triggers(
                device_id=device_id,
                entity_id=entity_id,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
        except Exception as e:
            return _error_response(str(e))

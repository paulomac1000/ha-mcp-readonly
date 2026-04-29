"""
Integration Analysis Tools (P3 - Nice to have).

Provides tools for integration analysis:
- get_integration_entities(domain)
- get_integration_summary(domain)
"""

import json
from collections import Counter
from typing import Any, Dict, List

from tools.utils import (
    get_best_name,
    get_registry_config_entries,
    get_registry_devices,
    get_registry_entities,
    make_ha_request,
)


def _get_entries_by_domain(entries: List[Dict[str, Any]], domain: str) -> List[Dict[str, Any]]:
    """Get all config entries for a domain."""
    return [entry for entry in entries if entry.get("domain") == domain]


def _get_entities_for_domain(entities: List[Dict[str, Any]], domain: str) -> List[Dict[str, Any]]:
    """Get all entities for a domain based on platform."""
    domain_entities = []
    for entity in entities:
        if entity.get("platform") == domain:
            domain_entities.append(entity)
    return domain_entities


def register_integration_tools(mcp, config_path: str, ha_url: str, ha_token: str) -> None:
    """Register integration analysis tools."""

    # =========================================================================
    # TOOLS
    # =========================================================================

    @mcp.tool(name="get_integration_entities_mcp_local_lan_mcp")
    async def get_integration_entities(domain: str, include_disabled: bool = False) -> str:
        """
        Get all entities for a given integration domain.

        Args:
            domain: Integration domain (e.g., "mqtt", "hue").
            include_disabled: Whether to include disabled entities.

        Returns:
            JSON string with domain summary, devices grouping, and availability stats.
        """
        # Get entities
        entities = _get_entities_for_domain(get_registry_entities(config_path), domain)

        if not entities:
            return json.dumps(
                {
                    "success": False,
                    "error": f"No entities found for integration '{domain}'",
                    "domain": domain,
                },
                indent=2,
            )

        # Get devices for context
        devices = get_registry_devices(config_path)
        devices_map = {d["id"]: d for d in devices}

        # Get live states
        states_map = {}
        if ha_url and ha_token:
            states_result = make_ha_request(ha_url, ha_token, "/api/states")
            if states_result.get("success"):
                states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

        # Process entities
        by_device = {}
        disabled_count = 0
        unavailable_count = 0

        # Add "No Device" category
        by_device["no_device"] = {"device_name": "No Device Assigned", "entities": []}

        for entity in entities:
            if entity.get("disabled_by"):
                disabled_count += 1
                if not include_disabled:
                    continue

            # Check state
            eid = entity.get("entity_id")
            state = states_map.get(eid, {}).get("state", "unknown")
            if state in ["unavailable", "unknown"] and not entity.get("disabled_by"):
                unavailable_count += 1

            # Prepare entity info
            entity_info = {
                "entity_id": eid,
                "name": get_best_name(entity, "entity"),
                "state": state,
                "disabled_by": entity.get("disabled_by"),
            }

            # Group by device
            device_id = entity.get("device_id")
            if device_id and device_id in devices_map:
                if device_id not in by_device:
                    device = devices_map[device_id]
                    by_device[device_id] = {
                        "device_name": get_best_name(device, "device"),
                        "model": device.get("model"),
                        "entities": [],
                    }
                by_device[device_id]["entities"].append(entity_info)
            else:
                by_device["no_device"]["entities"].append(entity_info)

        # Remove empty "No Device" category
        if not by_device["no_device"]["entities"]:
            del by_device["no_device"]

        return json.dumps(
            {
                "success": True,
                "domain": domain,
                "total_entities": len(entities),
                "returned_entities": sum(len(d["entities"]) for d in by_device.values()),
                "disabled_count": disabled_count,
                "unavailable_count": unavailable_count,
                "by_device": by_device,
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool(name="get_integration_summary_mcp_local_lan_mcp")
    async def get_integration_summary(domain: str) -> str:
        """
        Summarize an integration (devices, entities, health).

        Args:
            domain: Integration domain.

        Returns:
            JSON string with integration summary (entries, devices, entities, health).
        """
        entries = _get_entries_by_domain(get_registry_config_entries(config_path), domain)
        entities = _get_entities_for_domain(get_registry_entities(config_path), domain)

        if not entries and not entities:
            return json.dumps(
                {"success": False, "error": f"Integration '{domain}' not found"},
                indent=2,
            )

        # Analyze config entries
        entries_summary = {
            "total": len(entries),
            "loaded": 0,
            "disabled": 0,
            "titles": [e.get("title") for e in entries[:5]],
        }

        for entry in entries:
            if entry.get("disabled_by"):
                entries_summary["disabled"] += 1
            else:
                entries_summary["loaded"] += 1

        # Analyze entities
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

        entity_platforms = Counter()

        for entity in entities:
            platform = entity.get("entity_id").split(".")[0]
            entity_platforms[platform] += 1

            if entity.get("disabled_by"):
                entities_summary["disabled"] += 1
            else:
                entities_summary["enabled"] += 1
                state = states_map.get(entity.get("entity_id"), {}).get("state", "unknown")
                if state in ["unavailable", "unknown"]:
                    entities_summary["unavailable"] += 1
                else:
                    entities_summary["available"] += 1

        # Analyze devices
        devices = get_registry_devices(config_path)
        entry_ids = {e["entry_id"] for e in entries}
        domain_devices = [
            d
            for d in devices
            if any(entry_id in entry_ids for entry_id in d.get("config_entries", []))
        ]

        return json.dumps(
            {
                "success": True,
                "domain": domain,
                "config_entries": entries_summary,
                "devices_count": len(domain_devices),
                "entities_summary": entities_summary,
                "entity_platforms": dict(entity_platforms),
                "health": "Healthy" if entities_summary["unavailable"] == 0 else "Issues Detected",
            },
            indent=2,
            ensure_ascii=False,
        )

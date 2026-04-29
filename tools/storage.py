"""
Home Assistant Storage Tools
Reads .storage files for entity/device/area registries and other storage data.
Combines static registry data with live API states for optimal AI context.

DESIGN PRINCIPLES:
- Server-side filtering to minimize token usage
- Batch operations over multiple single calls
- Smart caching and data enrichment
- Automatic diagnostics and issue detection
"""

import json
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from tools.utils import get_best_name, load_registry, make_ha_request, resolve_area_id


def register_storage_tools(mcp, config_path: str, ha_url: str = None, ha_token: str = None):
    """
    Registers tools for working with .storage files and hybrid diagnostic tools.
    """

    # ========================================
    # 🚀 OPTIMIZED BATCH TOOLS
    # ========================================

    @mcp.tool()
    async def search_registries_batch(
        search_term: Optional[str] = None,
        entity_ids: Optional[str] = None,
        area_id: Optional[str] = None,
        device_id: Optional[str] = None,
        platform: Optional[str] = None,
        include_states: bool = False,
    ) -> str:
        """
        🚀 OPTIMIZED - searches multiple registries simultaneously.

        ~85% token savings for complex queries.
        Instead of: get_entity_registry() + get_device_registry() + filter × N

        Args:
            search_term: Searches in entity/device/area names
            entity_ids: Comma-separated list of entity ids
            area_id: Filter by area
            device_id: Filter by device
            platform: Filter by platform (e.g. "mqtt", "template")
            include_states: Whether to include live states from API (default: False)

        Returns:
            JSON with:
            - matched_entities: matching entities with full context
            - matched_devices: matching devices (if search_term)
            - matched_areas: matching areas (if search_term)
            - summary: statystyki

        Examples:
            search_registries_batch(search_term="temperature")
            search_registries_batch(entity_ids="sensor.temp1,sensor.temp2", include_states=True)
            search_registries_batch(area_id="living_room", platform="mqtt")
        """
        try:
            # Load registries using shared util (cached)
            ent_data = (
                load_registry("core.entity_registry", config_path)
                .get("data", {})
                .get("entities", [])
            )
            dev_data = (
                load_registry("core.device_registry", config_path)
                .get("data", {})
                .get("devices", [])
            )
            area_data = (
                load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
            )

            # Create lookup maps
            dev_map = {d["id"]: d for d in dev_data}
            area_map = {a["id"]: a for a in area_data}

            # Parse entity_ids filter
            target_eids = set(e.strip() for e in entity_ids.split(",")) if entity_ids else None
            term_lower = search_term.lower() if search_term else None

            matched_entities = []

            for entity in ent_data:
                eid = entity.get("entity_id")

                # Apply filters
                if target_eids and eid not in target_eids:
                    continue

                if platform and entity.get("platform") != platform:
                    continue

                # Resolve area (Entity area > Device area)
                final_area_id = resolve_area_id(entity, dev_map)

                if area_id and final_area_id != area_id:
                    continue

                if device_id and entity.get("device_id") != device_id:
                    continue

                # Text search
                if term_lower:
                    name = get_best_name(entity, "entity").lower()
                    if term_lower not in eid.lower() and term_lower not in name:
                        continue

                # Build enriched entity info
                entity_info = {
                    "entity_id": eid,
                    "name": get_best_name(entity, "entity"),
                    "platform": entity.get("platform"),
                    "device_id": entity.get("device_id"),
                    "area_id": final_area_id,
                    "disabled_by": entity.get("disabled_by"),
                    "hidden_by": entity.get("hidden_by"),
                    "unique_id": entity.get("unique_id"),
                }

                # Add device context
                did = entity.get("device_id")
                if did and did in dev_map:
                    device = dev_map[did]
                    entity_info["device"] = {
                        "name": get_best_name(device, "device"),
                        "manufacturer": device.get("manufacturer"),
                        "model": device.get("model"),
                    }

                # Add area context
                if final_area_id and final_area_id in area_map:
                    area = area_map[final_area_id]
                    entity_info["area"] = {
                        "name": area.get("name"),
                        "id": area.get("id"),
                    }

                matched_entities.append(entity_info)

            # Get live states if requested (BATCH)
            if include_states and matched_entities and ha_url and ha_token:
                states_result = make_ha_request(ha_url, ha_token, "/api/states")
                if states_result["success"]:
                    states_map = {s["entity_id"]: s for s in states_result["data"]}

                    for entity_info in matched_entities:
                        eid = entity_info["entity_id"]
                        if eid in states_map:
                            state_data = states_map[eid]
                            entity_info["state"] = {
                                "state": state_data.get("state"),
                                "last_changed": state_data.get("last_changed"),
                                "last_updated": state_data.get("last_updated"),
                            }

            # Search devices (if search_term provided)
            matched_devices = []
            if term_lower:
                for device in dev_data:
                    name = get_best_name(device, "device").lower()
                    manufacturer = (device.get("manufacturer") or "").lower()
                    model = (device.get("model") or "").lower()

                    if term_lower in name or term_lower in manufacturer or term_lower in model:
                        matched_devices.append(
                            {
                                "id": device.get("id"),
                                "name": get_best_name(device, "device"),
                                "manufacturer": device.get("manufacturer"),
                                "model": device.get("model"),
                                "area_id": device.get("area_id"),
                            }
                        )

            # Search areas (if search_term provided)
            matched_areas = []
            if term_lower:
                for area in area_data:
                    name = (area.get("name") or "").lower()
                    if term_lower in name:
                        matched_areas.append(area)

            # Limit results to prevent token overflow
            matched_entities = matched_entities[:100]
            matched_devices = matched_devices[:50]
            matched_areas = matched_areas[:20]

            return json.dumps(
                {
                    "success": True,
                    "search_params": {
                        "search_term": search_term,
                        "entity_ids": list(target_eids) if target_eids else None,
                        "area_id": area_id,
                        "device_id": device_id,
                        "platform": platform,
                        "include_states": include_states,
                    },
                    "summary": {
                        "matched_entities": len(matched_entities),
                        "matched_devices": len(matched_devices),
                        "matched_areas": len(matched_areas),
                    },
                    "matched_entities": matched_entities,
                    "matched_devices": matched_devices if matched_devices else None,
                    "matched_areas": matched_areas if matched_areas else None,
                },
                indent=2,
                ensure_ascii=False,
            )

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    async def get_entity_context(entity_id: str) -> str:
        """
        🚀 CONTEXTUALIZED DIAGNOSTICS - Comprehensive entity context.

        ~75% token savings when analyzing an entity.
        Instead of: get_entity_details() + get_entity_state() + search related entities

        Args:
            entity_id: Entity id (np. "sensor.temperature_living_room")

        Returns:
            JSON with:
            - entity_info: basic info from registry
            - current_state: current state from live API
            - device_info: device info
            - area_info: area info
            - related_entities: other entities from the same device
            - area_entities: other entities from the same area (top 10)
            - integration_info: integration info
            - issues: found issues
            - recommendations: suggestions
        """
        try:
            # Load registries
            ent_data = (
                load_registry("core.entity_registry", config_path)
                .get("data", {})
                .get("entities", [])
            )
            dev_data = (
                load_registry("core.device_registry", config_path)
                .get("data", {})
                .get("devices", [])
            )
            area_data = (
                load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
            )
            config_data = (
                load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])
            )

            # Find entity
            entity = next((e for e in ent_data if e.get("entity_id") == entity_id), None)

            if not entity:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Entity '{entity_id}' not found in registry",
                    },
                    indent=2,
                )

            result = {
                "success": True,
                "entity_id": entity_id,
                "entity_info": {
                    "name": get_best_name(entity, "entity"),
                    "platform": entity.get("platform"),
                    "device_id": entity.get("device_id"),
                    "area_id": entity.get("area_id"),
                    "disabled_by": entity.get("disabled_by"),
                    "hidden_by": entity.get("hidden_by"),
                    "unique_id": entity.get("unique_id"),
                    "config_entry_id": entity.get("config_entry_id"),
                },
                "current_state": None,
                "device_info": None,
                "area_info": None,
                "related_entities": [],
                "area_entities": [],
                "integration_info": None,
                "issues": [],
                "recommendations": [],
            }

            # Get live state
            if ha_url and ha_token:
                state_res = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")
                if state_res["success"]:
                    s = state_res["data"]
                    result["current_state"] = {
                        "state": s.get("state"),
                        "last_changed": s.get("last_changed"),
                        "last_updated": s.get("last_updated"),
                        "attributes": {
                            k: v
                            for k, v in s.get("attributes", {}).items()
                            if k
                            in [
                                "unit_of_measurement",
                                "device_class",
                                "friendly_name",
                                "current_temperature",
                                "brightness",
                                "battery_level",
                                "temperature",
                                "humidity",
                                "illuminance",
                            ]
                        },
                    }

                    # Check for issues
                    state_val = s.get("state")
                    if state_val == "unavailable":
                        result["issues"].append("Entity is UNAVAILABLE")
                        result["recommendations"].append(
                            "Check device connection and integration status"
                        )
                    elif state_val == "unknown":
                        result["issues"].append("Entity state is UNKNOWN")
                        result["recommendations"].append("Entity may not have reported state yet")
                else:
                    result["issues"].append(f"Could not fetch live state: {state_res.get('error')}")

            # Get device info
            did = entity.get("device_id")
            if did:
                device = next((d for d in dev_data if d.get("id") == did), None)
                if device:
                    result["device_info"] = {
                        "id": device.get("id"),
                        "name": get_best_name(device, "device"),
                        "manufacturer": device.get("manufacturer"),
                        "model": device.get("model"),
                        "sw_version": device.get("sw_version"),
                        "area_id": device.get("area_id"),
                        "disabled_by": device.get("disabled_by"),
                    }

                    # Find related entities (same device)
                    for other in ent_data:
                        if other.get("device_id") == did and other.get("entity_id") != entity_id:
                            result["related_entities"].append(
                                {
                                    "entity_id": other.get("entity_id"),
                                    "platform": other.get("platform"),
                                    "name": get_best_name(other, "entity"),
                                    "disabled": other.get("disabled_by") is not None,
                                }
                            )

            # Resolve area (Entity area > Device area)
            dev_map = {d["id"]: d for d in dev_data}
            final_area_id = resolve_area_id(entity, dev_map)

            if final_area_id:
                area = next((a for a in area_data if a.get("id") == final_area_id), None)
                if area:
                    result["area_info"] = area

                    # Find area entities (top 10)
                    area_entities_list = []
                    for other in ent_data:
                        other_area_id = resolve_area_id(other, dev_map)
                        if other_area_id == final_area_id and other.get("entity_id") != entity_id:
                            area_entities_list.append(
                                {
                                    "entity_id": other.get("entity_id"),
                                    "platform": other.get("platform"),
                                    "name": get_best_name(other, "entity"),
                                }
                            )

                    result["area_entities"] = area_entities_list[:10]
                    if len(area_entities_list) > 10:
                        result["area_entities"].append(
                            {
                                "note": f"... and {len(area_entities_list) - 10} more entities in this area"
                            }
                        )

            # Get integration info
            config_entry_id = entity.get("config_entry_id")
            if config_entry_id:
                config_entry = next(
                    (c for c in config_data if c.get("entry_id") == config_entry_id),
                    None,
                )
                if config_entry:
                    result["integration_info"] = {
                        "domain": config_entry.get("domain"),
                        "title": config_entry.get("title"),
                        "version": config_entry.get("version"),
                        "source": config_entry.get("source"),
                        "disabled": config_entry.get("disabled_by") is not None,
                    }

            # Additional checks
            if entity.get("disabled_by"):
                result["issues"].append(f"Entity is DISABLED by: {entity.get('disabled_by')}")
                result["recommendations"].append("Enable entity in entity registry if needed")

            if entity.get("hidden_by"):
                result["issues"].append(f"Entity is HIDDEN by: {entity.get('hidden_by')}")

            if not result["issues"]:
                result["recommendations"].append("Entity appears to be configured correctly")

            return json.dumps(result, indent=2, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    async def get_area_overview(area_id: str) -> str:
        """
        🚀 BATCH ENDPOINT - Comprehensive area overview.

        ~70% token savings when analyzing an area.
        Zamiast: get_area_registry() + filter entities + get states × N

        Args:
            area_id: Area id or name (e.g. "living_room", "kitchen")

        Returns:
            JSON with:
            - area_info: basic area info
            - devices_count: number of devices
            - entities_by_domain: entity breakdown by domains
            - entities_summary: list of entities with current states
            - unavailable_entities: list of unavailable entities
            - sensor_readings: grouped sensor readings
            - issues: found issues
        """
        try:
            # Load registries
            area_data = (
                load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
            )
            dev_data = (
                load_registry("core.device_registry", config_path)
                .get("data", {})
                .get("devices", [])
            )
            ent_data = (
                load_registry("core.entity_registry", config_path)
                .get("data", {})
                .get("entities", [])
            )

            # Find area
            area = next(
                (
                    a
                    for a in area_data
                    if a.get("id") == area_id or a.get("name", "").lower() == area_id.lower()
                ),
                None,
            )

            if not area:
                available_areas = [{"id": a["id"], "name": a["name"]} for a in area_data]
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Area '{area_id}' not found",
                        "available_areas": available_areas,
                    },
                    indent=2,
                )

            final_area_id = area.get("id")

            result = {
                "area_info": area,
                "devices_count": 0,
                "entities_by_domain": {},
                "entities_summary": [],
                "unavailable_entities": [],
                "sensor_readings": {},
                "issues": [],
            }

            # Count devices in area
            area_devices = [d["id"] for d in dev_data if d.get("area_id") == final_area_id]
            result["devices_count"] = len(area_devices)

            # Find entities (assigned to area OR to devices in area)
            dev_map = {d["id"]: d for d in dev_data}
            area_entities = []
            for e in ent_data:
                resolved_area = resolve_area_id(e, dev_map)
                if resolved_area == final_area_id:
                    area_entities.append(e)

            # Group by domain
            for entity in area_entities:
                domain = entity.get("entity_id", "").split(".")[0]
                result["entities_by_domain"][domain] = (
                    result["entities_by_domain"].get(domain, 0) + 1
                )

            # Get live states (BATCH)
            if ha_url and ha_token and area_entities:
                states_res = make_ha_request(ha_url, ha_token, "/api/states")
                if states_res["success"]:
                    states_map = {s["entity_id"]: s for s in states_res["data"]}

                    for entity in area_entities[:50]:  # Limit to 50
                        eid = entity.get("entity_id")
                        if eid not in states_map:
                            continue

                        state_data = states_map[eid]
                        state_val = state_data.get("state")
                        name = get_best_name(entity, "entity")

                        summary_item = {
                            "entity_id": eid,
                            "name": name,
                            "state": state_val,
                            "platform": entity.get("platform"),
                        }
                        result["entities_summary"].append(summary_item)

                        # Check for issues
                        if state_val == "unavailable":
                            result["unavailable_entities"].append(eid)
                            result["issues"].append(f"{name} ({eid}) is unavailable")
                        elif state_val == "unknown":
                            result["issues"].append(f"{name} ({eid}) has unknown state")

                        # Extract sensor readings
                        if state_val not in ["unavailable", "unknown", "on", "off", ""]:
                            try:
                                float(state_val)
                                unit = state_data.get("attributes", {}).get(
                                    "unit_of_measurement", ""
                                )
                                domain = eid.split(".")[0]
                                key = f"{domain}"
                                if unit:
                                    key += f" ({unit})"

                                if key not in result["sensor_readings"]:
                                    result["sensor_readings"][key] = []
                                result["sensor_readings"][key].append(f"{name}: {state_val}")
                            except ValueError:
                                pass

                    if len(area_entities) > 50:
                        result["entities_summary"].append(
                            {"note": f"... and {len(area_entities) - 50} more entities"}
                        )

            # Additional checks
            if result["devices_count"] == 0 and len(area_entities) > 0:
                result["issues"].append("Area has entities but no devices assigned directly")

            return json.dumps(result, indent=2, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    async def get_history_stats(entity_id: str, hours_back: int = 24) -> str:
        """
        📊 SMART HISTORY - Entity history statistics.

        Returns processed data (Min, Max, Average) instead of raw list.
        ~90% token savings when analyzing history.

        Args:
            entity_id: Entity id.
            hours_back: How many hours back to analyze (default: 24, max: 168).

        Returns:
            JSON with analysis (numeric or categorical).
        """
        if not ha_url or not ha_token:
            return json.dumps({"error": "HA API not configured"}, indent=2)

        # Limit to 1 week
        hours_back = min(hours_back, 168)

        start_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        url = f"/api/history/period/{start_time.isoformat()}?filter_entity_id={entity_id}&minimal_response=true"

        res = make_ha_request(ha_url, ha_token, url)
        if not res["success"] or not res["data"] or not res["data"][0]:
            return json.dumps({"error": "No history data found"}, indent=2)

        history = res["data"][0]
        states = [h["state"] for h in history if h["state"] not in ["unavailable", "unknown"]]

        if not states:
            return json.dumps({"info": "No valid states in period"}, indent=2)

        try:
            # Numeric analysis
            nums = [float(s) for s in states]
            analysis = {
                "type": "numeric",
                "min": min(nums),
                "max": max(nums),
                "avg": round(statistics.mean(nums), 2),
                "median": round(statistics.median(nums), 2),
                "current": nums[-1],
                "samples": len(nums),
                "period_hours": hours_back,
            }
        except ValueError:
            # Categorical analysis (e.g., on/off)
            counts = Counter(states)
            total = len(states)
            analysis = {
                "type": "categorical",
                "most_common": counts.most_common(1)[0][0],
                "distribution": {
                    k: {"count": v, "percentage": round(v / total * 100, 1)}
                    for k, v in counts.items()
                },
                "samples": total,
                "period_hours": hours_back,
            }

        return json.dumps(
            {"entity_id": entity_id, "analysis": analysis}, indent=2, ensure_ascii=False
        )

    # ========================================
    # ⚙️ REGISTRY DUMP TOOLS
    # ========================================

    @mcp.tool()
    async def get_entity_registry() -> str:
        """
        Fetches registry of all entities from .storage.
        Contains: entity_id, platform, device_id, aliases, disabled_by, hidden_by.

        ⚠️ Warning: returns all entities - use search_registries_batch() for filtering.
        """
        data = (
            load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
        )
        simplified = []
        for e in data:
            simplified.append(
                {
                    "entity_id": e.get("entity_id"),
                    "name": get_best_name(e, "entity"),
                    "platform": e.get("platform"),
                    "device_id": e.get("device_id"),
                    "area_id": e.get("area_id"),
                    "disabled_by": e.get("disabled_by"),
                    "hidden_by": e.get("hidden_by"),
                    "unique_id": e.get("unique_id"),
                    "config_entry_id": e.get("config_entry_id"),
                }
            )
        return json.dumps(
            {"total_entities": len(simplified), "entities": simplified},
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def get_device_registry() -> str:
        """
        Fetches registry of all devices from .storage.
        Contains: name, manufacturer, model, sw_version, connections, identifiers.
        """
        data = load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
        simplified = []
        for d in data:
            simplified.append(
                {
                    "id": d.get("id"),
                    "name": get_best_name(d, "device"),
                    "manufacturer": d.get("manufacturer"),
                    "model": d.get("model"),
                    "sw_version": d.get("sw_version"),
                    "area_id": d.get("area_id"),
                    "disabled_by": d.get("disabled_by"),
                    "config_entries": d.get("config_entries", []),
                }
            )
        return json.dumps(
            {"total_devices": len(simplified), "devices": simplified},
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def get_area_registry() -> str:
        """
        Fetches registry of all areas/rooms from .storage.
        Contains: name, aliases, picture, icon.
        """
        data = load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
        return json.dumps({"total_areas": len(data), "areas": data}, indent=2, ensure_ascii=False)

    @mcp.tool()
    async def get_config_entries() -> str:
        """
        Fetches all configuration entries of integrations.
        Shows installed integrations, their domains, titles, versions, options.
        """
        data = load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])
        simplified = []
        for entry in data:
            simplified.append(
                {
                    "entry_id": entry.get("entry_id"),
                    "domain": entry.get("domain"),
                    "title": entry.get("title"),
                    "version": entry.get("version"),
                    "source": entry.get("source"),
                    "disabled_by": entry.get("disabled_by"),
                    "options": entry.get("options", {}),
                }
            )
        return json.dumps(
            {"total_entries": len(simplified), "entries": simplified},
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def get_lovelace_dashboards() -> str:
        """
        Fetches list of all Lovelace dashboards.
        """
        data = load_registry("lovelace.dashboards", config_path).get("data", {}).get("items", [])
        return json.dumps(
            {"total_dashboards": len(data), "dashboards": data},
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def get_lovelace_config(dashboard: str = "lovelace") -> str:
        """
        Fetches configuration of a specific Lovelace dashboard.

        Args:
            dashboard: Dashboard name (default: "lovelace" for main)
        """
        registry_name = f"lovelace.{dashboard}" if dashboard != "lovelace" else "lovelace"
        data = load_registry(registry_name, config_path)
        if not data:
            return json.dumps({"error": f"Dashboard '{dashboard}' not found"}, indent=2)
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    async def get_exposed_entities() -> str:
        """
        Fetches list of entities exposed to voice assistants (Google Assistant, Alexa).
        """
        data = (
            load_registry("cloud.google_assistant", config_path).get("data", {}).get("entities", {})
        )
        return json.dumps(
            {"total_exposed": len(data), "entities": data}, indent=2, ensure_ascii=False
        )

    @mcp.tool()
    async def get_persons() -> str:
        """
        Fetches list of people (person entities) with their configuration.
        """
        data = load_registry("person", config_path).get("data", {}).get("items", [])
        return json.dumps(
            {"total_persons": len(data), "persons": data}, indent=2, ensure_ascii=False
        )

    @mcp.tool()
    async def get_zones() -> str:
        """
        Fetches list of zones with their configuration.
        """
        data = load_registry("zone", config_path).get("data", {}).get("items", [])
        return json.dumps({"total_zones": len(data), "zones": data}, indent=2, ensure_ascii=False)

    @mcp.tool()
    async def get_input_helpers() -> str:
        """
        Fetches all input helpers (input_boolean, input_number, input_select, input_text, input_datetime, input_button).
        """
        helpers = {}
        types = [
            "input_boolean",
            "input_number",
            "input_select",
            "input_text",
            "input_datetime",
            "input_button",
        ]

        for helper_type in types:
            items = load_registry(helper_type, config_path).get("data", {}).get("items", [])
            if items:
                helpers[helper_type] = {"count": len(items), "items": items}

        return json.dumps(helpers, indent=2, ensure_ascii=False)

    @mcp.tool()
    async def get_hacs_data() -> str:
        """
        Fetches HACS data (Home Assistant Community Store).
        Shows installed custom integrations and themes.
        """
        data = load_registry("hacs.data", config_path)
        if not data:
            return json.dumps({"info": "HACS not installed or storage file not found"}, indent=2)
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.tool()
    async def get_timers() -> str:
        """
        Fetches list of timers with their configuration.
        """
        data = load_registry("timer", config_path).get("data", {}).get("items", [])
        return json.dumps({"total_timers": len(data), "timers": data}, indent=2, ensure_ascii=False)

    @mcp.tool()
    async def get_counters() -> str:
        """
        Fetches list of counters with their configuration.
        """
        data = load_registry("counter", config_path).get("data", {}).get("items", [])
        return json.dumps(
            {"total_counters": len(data), "counters": data},
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def get_template_entities() -> str:
        """
        Fetches all template sensors and binary_sensors created via UI.
        Template helpers are stored in .storage/core.config_entries.

        Returns:
            List of template entities with their configuration (template code, device_class, etc.)
        """
        data = load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])
        templates = []

        def slugify(text):
            if not text:
                return "unknown"
            text = text.lower()
            text = re.sub(r"[^a-z0-9]+", "_", text)
            return text.strip("_")

        for entry in data:
            if entry.get("domain") != "template":
                continue

            opts = entry.get("options", {})
            name = entry.get("title") or opts.get("name", "unknown")
            ttype = opts.get("template_type", "sensor")

            templates.append(
                {
                    "name": name,
                    "entity_id": f"{ttype}.{slugify(name)}",
                    "template_type": ttype,
                    "state_template": opts.get("state", ""),
                    "device_class": opts.get("device_class"),
                    "unit_of_measurement": opts.get("unit_of_measurement"),
                    "icon": opts.get("icon"),
                    "availability_template": opts.get("availability"),
                    "attribute_templates": opts.get("attributes", {}),
                    "device_id": opts.get("device_id"),
                    "created_at": entry.get("created_at"),
                    "modified_at": entry.get("modified_at"),
                    "disabled": entry.get("disabled_by") is not None,
                    "entry_id": entry.get("entry_id"),
                }
            )

        templates.sort(key=lambda x: x.get("name", "").lower())

        return json.dumps(
            {"total_templates": len(templates), "templates": templates},
            indent=2,
            ensure_ascii=False,
        )

    # ========================================
    # 🔄 COMPATIBILITY WRAPPERS
    # ========================================

    @mcp.tool()
    async def search_entity_by_name(search_term: str) -> str:
        """
        Searches entities by name or entity_id in the registry.

        ⚠️ Warning: Use search_registries_batch() for more advanced searching.

        Args:
            search_term: Phrase to search for
        """
        return await search_registries_batch(search_term=search_term)

    @mcp.tool()
    async def get_entity_details(entity_id: str) -> str:
        """
        Fetches detailed information about a specific entity from the registry.
        Contains related device and area.

        ⚠️ Warning: Use get_entity_context() for full context with live state.

        Args:
            entity_id: Entity id (np. "sensor.temperature_living_room")
        """
        return await get_entity_context(entity_id)

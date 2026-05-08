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
        Search multiple registries (entity, device, area) simultaneously by name, entity_ids, area, device, or platform. ~85% token savings.

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
            - summary: statistics

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
        Get comprehensive entity context: state, registry info, related entities, automations, and device details. ~75% token savings.

        ~75% token savings when analyzing an entity.
        Instead of: get_entity_details() + get_entity_state() + search related entities

        Args:
            entity_id: Entity id (e.g. "sensor.temperature_living_room")

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
        BATCH ENDPOINT - Comprehensive area overview.

        ~70% token savings when analyzing an area.
        Instead of: get_area_registry() + filter entities + get states × N

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
        Get entity history statistics: min, max, average values from the recorder. ~90% token savings vs raw history.

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

        Warning: returns all entities - use search_registries_batch() for filtering.
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
        data = load_registry("lovelace_dashboards", config_path).get("data", {}).get("items", [])
        return json.dumps(
            {"success": True, "total_dashboards": len(data), "dashboards": data},
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
        registry_name = _resolve_dashboard_registry(dashboard)
        data = load_registry(registry_name, config_path)
        if not data:
            return json.dumps({"error": f"Dashboard '{dashboard}' not found"}, indent=2)
        return json.dumps(data, indent=2, ensure_ascii=False)

    def _resolve_dashboard_registry(dashboard: str) -> str:
        """Resolve a dashboard identifier to a .storage registry name."""
        if dashboard.startswith("lovelace."):
            return dashboard
        dashboards_data = (
            load_registry("lovelace_dashboards", config_path).get("data", {}).get("items", [])
        )
        for d in dashboards_data:
            if d.get("url_path") == dashboard or d.get("id") == dashboard:
                resolved = d.get("id", dashboard)
                return f"lovelace.{resolved}"
        return f"lovelace.{dashboard}"

    def _get_dashboards_registry() -> list:
        """Load dashboards registry and return items list."""
        return load_registry("lovelace_dashboards", config_path).get("data", {}).get("items", [])

    def _get_lovelace_cards(dashboard_id: str) -> list:
        """Extract flat list of card dicts from a dashboard config, with position info."""
        registry_name = _resolve_dashboard_registry(dashboard_id)
        config = load_registry(registry_name, config_path)
        if not config:
            return []
        views = config.get("data", {}).get("config", {}).get("views", [])
        cards_with_pos = []
        for view_idx, view in enumerate(views):
            for card_idx, card in enumerate(view.get("cards", [])):
                card_copy = dict(card)
                card_copy["_view_idx"] = view_idx
                card_copy["_view_title"] = view.get("title", f"View {view_idx + 1}")
                card_copy["_view_path"] = view.get("path", "")
                card_copy["_card_idx"] = card_idx
                cards_with_pos.append(card_copy)
            for badge_idx, badge in enumerate(view.get("badges", [])):
                badge_copy = dict(badge) if isinstance(badge, dict) else {"entity": badge}
                badge_copy["_view_idx"] = view_idx
                badge_copy["_view_title"] = view.get("title", f"View {view_idx + 1}")
                badge_copy["_view_path"] = view.get("path", "")
                badge_copy["_badge_idx"] = badge_idx
                badge_copy["_is_badge"] = True
                cards_with_pos.append(badge_copy)
        return cards_with_pos

    @mcp.tool()
    async def get_lovelace_resources() -> str:
        """
        Fetches list of registered Lovelace resources (custom cards, JS modules, CSS).

        Returns:
            JSON with resource list, type breakdown, and source classification.
        """
        data = load_registry("lovelace_resources", config_path).get("data", {}).get("items", [])
        by_type = {}
        for r in data:
            rtype = r.get("type", "unknown")
            by_type[rtype] = by_type.get(rtype, 0) + 1
        return json.dumps(
            {
                "success": True,
                "total_resources": len(data),
                "resources": data,
                "by_type": by_type,
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def search_lovelace_config(
        search_term: str = None,
        card_type: str = None,
        entity_id: str = None,
        dashboard: str = None,
        max_results: int = 50,
    ) -> str:
        """
        Search inside Lovelace dashboard configurations by entity_id, card_type, or free-text.

        Searches cards/badges across all dashboards by entity_id, card_type,
        or free-text search_term. ~90% token savings vs returning full configs.

        Args:
            search_term: Free-text search in card JSON (case-insensitive).
            card_type: Filter by card type (e.g. "tile", "custom:mushroom-light-card").
            entity_id: Find cards referencing this entity.
            dashboard: Limit search to a specific dashboard (url_path or id).
            max_results: Maximum results (default 50).
        """
        try:
            matches = []
            dashboards_data = _get_dashboards_registry()

            target_ids = []
            if dashboard:
                for d in dashboards_data:
                    if d.get("url_path") == dashboard or d.get("id") == dashboard:
                        target_ids.append(d.get("id", ""))
                if not target_ids:
                    return json.dumps(
                        {
                            "success": True,
                            "matched_count": 0,
                            "matches": [],
                            "warnings": [f"Dashboard '{dashboard}' not found"],
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
            else:
                target_ids = [d.get("id", "") for d in dashboards_data]

            dashboards_scanned = 0
            warnings = []

            for dash_id in target_ids:
                if not dash_id:
                    continue
                try:
                    cards = _get_lovelace_cards(dash_id)
                except Exception:
                    warnings.append(f"Could not scan dashboard '{dash_id}'")
                    continue

                if not cards:
                    warnings.append(f"Dashboard '{dash_id}' is strategy-based — no cards to search")
                    continue

                dashboards_scanned += 1

                for card in cards:
                    if len(matches) >= max_results:
                        break

                    card_str = json.dumps(card).lower()
                    matched = False
                    matched_by = []

                    if search_term and search_term.lower() in card_str:
                        matched = True
                        matched_by.append("search_term")
                    if card_type and card.get("type", "").lower() == card_type.lower():
                        matched = True
                        matched_by.append("card_type")
                    if entity_id:
                        if card.get("entity") == entity_id:
                            matched = True
                            matched_by.append("entity_id")
                        elif "entities" in card:
                            entities_list = card["entities"]
                            for e in entities_list:
                                if e == entity_id or (
                                    isinstance(e, dict) and e.get("entity") == entity_id
                                ):
                                    matched = True
                                    matched_by.append("entity_id")
                                    break
                        if not matched:
                            card_json = json.dumps(card)
                            if entity_id in card_json:
                                matched = True
                                matched_by.append("entity_in_card_attr")

                    if matched or (not search_term and not card_type and not entity_id):
                        if not search_term and not card_type and not entity_id:
                            break
                        match_info = {
                            "dashboard": dash_id,
                            "view_title": card.get("_view_title", ""),
                            "view_path": card.get("_view_path", ""),
                            "card_type": card.get("type", "unknown"),
                            "card_index": card.get("_card_idx", card.get("_badge_idx", 0)),
                            "is_badge": card.get("_is_badge", False),
                            "matched_by": matched_by,
                        }
                        if entity_id:
                            match_info["entity_id"] = entity_id
                        if card.get("name"):
                            match_info["card_name"] = card["name"]
                        if card.get("title"):
                            match_info["card_title"] = card["title"]
                        matches.append(match_info)

                if len(matches) >= max_results:
                    break

            return json.dumps(
                {
                    "success": True,
                    "matched_count": len(matches),
                    "dashboards_scanned": dashboards_scanned,
                    "dashboards_skipped": len(target_ids) - dashboards_scanned,
                    "matches": matches,
                    "warnings": warnings if warnings else None,
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {"success": False, "error": str(e)},
                indent=2,
            )

    @mcp.tool()
    async def get_lovelace_config_summary(dashboard: str = None) -> str:
        """
        Token-efficient Lovelace dashboard structure overview: card type breakdown, view counts, strategy detection. ~95% token savings.

        Returns card counts, type breakdown, view info. ~95% token savings
        vs returning full dashboard JSON.

        Args:
            dashboard: Specific dashboard url_path/id, or None for all dashboards.
        """
        try:
            dashboards_data = _get_dashboards_registry()

            if dashboard:
                target = None
                for d in dashboards_data:
                    if d.get("url_path") == dashboard or d.get("id") == dashboard:
                        target = d
                        break
                if not target:
                    return json.dumps(
                        {"success": False, "error": f"Dashboard '{dashboard}' not found"},
                        indent=2,
                    )
                dashboards_data = [target]

            summaries = []
            for d in dashboards_data:
                dash_id = d.get("id", "")
                info = {
                    "id": dash_id,
                    "url_path": d.get("url_path", ""),
                    "title": d.get("title", ""),
                    "mode": d.get("mode", "unknown"),
                    "show_in_sidebar": d.get("show_in_sidebar", True),
                    "icon": d.get("icon"),
                    "views": [],
                    "total_views": 0,
                    "total_cards": 0,
                    "total_badges": 0,
                    "card_types_breakdown": {},
                    "strategy": None,
                }

                try:
                    cards = _get_lovelace_cards(dash_id)
                except Exception:
                    info["error"] = "Could not load dashboard config"
                    summaries.append(info)
                    continue

                if not cards:
                    config = load_registry(_resolve_dashboard_registry(dash_id), config_path)
                    strategy = config.get("data", {}).get("config", {}).get("strategy", {})
                    if strategy:
                        info["strategy"] = strategy.get("type", "unknown")
                    summaries.append(info)
                    continue

                views_map = {}
                badges_count = 0
                card_types = {}
                for card in cards:
                    if card.get("_is_badge"):
                        badges_count += 1
                    else:
                        ctype = card.get("type", "unknown")
                        card_types[ctype] = card_types.get(ctype, 0) + 1
                    vi = card.get("_view_idx", 0)
                    if vi not in views_map:
                        views_map[vi] = {
                            "title": card.get("_view_title", ""),
                            "path": card.get("_view_path", ""),
                            "cards": 0,
                        }
                    if not card.get("_is_badge"):
                        views_map[vi]["cards"] += 1

                info["views"] = list(views_map.values())
                info["total_views"] = len(views_map)
                info["total_cards"] = sum(v["cards"] for v in views_map.values())
                info["total_badges"] = badges_count
                info["card_types_breakdown"] = dict(sorted(card_types.items(), key=lambda x: -x[1]))
                summaries.append(info)

            if dashboard and len(summaries) == 1:
                return json.dumps(
                    {"success": True, "dashboard": summaries[0]},
                    indent=2,
                    ensure_ascii=False,
                )

            total_views = sum(s["total_views"] for s in summaries if "total_views" in s)
            total_cards = sum(s["total_cards"] for s in summaries if "total_cards" in s)
            strategy_dashboards = [s["id"] for s in summaries if s.get("strategy")]
            yaml_dashboards = [s["id"] for s in summaries if s.get("mode") == "yaml"]

            global_ct = {}
            for s in summaries:
                for ct, cnt in s.get("card_types_breakdown", {}).items():
                    global_ct[ct] = global_ct.get(ct, 0) + cnt

            return json.dumps(
                {
                    "success": True,
                    "total_dashboards": len(summaries),
                    "dashboards_summary": summaries,
                    "global_stats": {
                        "total_views": total_views,
                        "total_cards": total_cards,
                        "top_card_types": dict(sorted(global_ct.items(), key=lambda x: -x[1])[:10]),
                        "strategy_dashboards": strategy_dashboards,
                        "yaml_mode_dashboards": yaml_dashboards,
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {"success": False, "error": str(e)},
                indent=2,
            )

    @mcp.tool()
    async def diagnose_lovelace_setup() -> str:
        """
        Full Lovelace diagnostics: missing entity references, strategy/YAML mode, resource analysis, recommendations. ~85% token savings.

        Checks: missing entity references, strategy/YAML mode dashboards,
        resource analysis, and generates recommendations. ~85% token savings
        vs manual multi-call workflow.
        """
        try:
            dashboards_data = _get_dashboards_registry()
            resources_data = (
                load_registry("lovelace_resources", config_path).get("data", {}).get("items", [])
            )

            entity_registry = (
                load_registry("core.entity_registry", config_path)
                .get("data", {})
                .get("entities", [])
            )
            known_entity_ids = {e.get("entity_id", "") for e in entity_registry}

            missing_refs = []
            strategy_dashboards = []
            yaml_dashboards = []
            dashboard_stats = []
            all_referenced_entities = set()

            for d in dashboards_data:
                dash_id = d.get("id", "")
                info = {
                    "id": dash_id,
                    "url_path": d.get("url_path", ""),
                    "title": d.get("title", ""),
                    "mode": d.get("mode", "unknown"),
                    "cards_count": 0,
                    "views_count": 0,
                    "is_strategy": False,
                    "strategy_type": None,
                }

                if d.get("mode") == "yaml":
                    yaml_dashboards.append(dash_id)
                    info["is_yaml"] = True
                    dashboard_stats.append(info)
                    continue

                try:
                    cards = _get_lovelace_cards(dash_id)
                except Exception:
                    dashboard_stats.append(info)
                    continue

                if not cards:
                    config = load_registry(_resolve_dashboard_registry(dash_id), config_path)
                    strategy = config.get("data", {}).get("config", {}).get("strategy", {})
                    if strategy:
                        info["is_strategy"] = True
                        info["strategy_type"] = strategy.get("type", "unknown")
                        strategy_dashboards.append(dash_id)
                    dashboard_stats.append(info)
                    continue

                views = set()
                card_entities = []
                for card in cards:
                    if not card.get("_is_badge"):
                        info["cards_count"] += 1
                    cid = card.get("entity")
                    if cid:
                        card_entities.append(cid)
                    if "entities" in card:
                        for e in card["entities"]:
                            eid = e.get("entity", e) if isinstance(e, dict) else e
                            if isinstance(eid, str):
                                card_entities.append(eid)
                    views.add(card.get("_view_idx", 0))

                    card_json = json.dumps(card)
                    for eid in known_entity_ids:
                        if eid in card_json:
                            all_referenced_entities.add(eid)

                info["views_count"] = len(views)

                for eid in card_entities:
                    if eid not in known_entity_ids:
                        missing_refs.append(
                            {
                                "entity_id": eid,
                                "dashboard": dash_id,
                                "dashboard_title": d.get("title", ""),
                            }
                        )

                dashboard_stats.append(info)

            resource_urls = [r.get("url", "").split("?")[0].split("/")[-1] for r in resources_data]

            issues = []
            recommendations = []

            if missing_refs:
                unique_missing = {}
                for mr in missing_refs:
                    eid = mr["entity_id"]
                    if eid not in unique_missing:
                        unique_missing[eid] = []
                    unique_missing[eid].append(mr["dashboard"])
                for eid, dashbs in unique_missing.items():
                    issues.append(
                        f"Entity '{eid}' referenced in {len(dashbs)} dashboard(s) "
                        f"does not exist in registry"
                    )
                    recommendations.append(
                        f"Remove or replace references to '{eid}' in dashboards: "
                        f"{', '.join(dashbs)}"
                    )

            if strategy_dashboards:
                issues.append(
                    f"{len(strategy_dashboards)} strategy-based dashboard(s): "
                    f"{', '.join(strategy_dashboards)}"
                )
                recommendations.append(
                    "Strategy-based dashboards have no explicit card data — "
                    "cards are generated dynamically by Home Assistant"
                )

            if yaml_dashboards:
                issues.append(
                    f"{len(yaml_dashboards)} YAML-mode dashboard(s): {', '.join(yaml_dashboards)}"
                )
                recommendations.append(
                    "YAML-mode dashboards are read-only via storage; only file-based "
                    "configs can provide card data"
                )

            return json.dumps(
                {
                    "success": True,
                    "dashboards": dashboard_stats,
                    "resources": {
                        "total": len(resources_data),
                        "items": resources_data,
                        "filenames": resource_urls,
                    },
                    "health_checks": {
                        "missing_entity_references": missing_refs[:50],
                        "missing_entity_count": len(set(m["entity_id"] for m in missing_refs)),
                        "total_referenced_entities": len(all_referenced_entities),
                        "strategy_dashboards": strategy_dashboards,
                        "yaml_mode_dashboards": yaml_dashboards,
                    },
                    "issues": issues,
                    "recommendations": recommendations,
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {"success": False, "error": str(e)},
                indent=2,
            )

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
            {"success": True, "total_persons": len(data), "persons": data},
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def get_zones() -> str:
        """
        Fetches list of zones with their configuration.
        """
        data = load_registry("zone", config_path).get("data", {}).get("items", [])
        return json.dumps(
            {"success": True, "total_zones": len(data), "zones": data}, indent=2, ensure_ascii=False
        )

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
            return json.dumps(
                {"success": True, "info": "HACS not installed or storage file not found"}, indent=2
            )
        return json.dumps({"success": True, **data}, indent=2, ensure_ascii=False)

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
    async def get_template_entities(entity_id: str = None) -> str:
        """
        Fetches template sensors and binary_sensors created via UI.
        Template helpers are stored in .storage/core.config_entries.

        Args:
            entity_id: Optional filter by entity_id (e.g. "sensor.my_template").
                       When provided, returns only the matching template.

        Returns:
            JSON with total_templates, templates list, and their configuration.
        """
        data = load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])
        templates = []

        # Build entity_id lookup from entity registry for accurate matching
        entity_reg = load_registry("core.entity_registry", config_path)
        entry_to_entity = {}
        for ent in entity_reg.get("data", {}).get("entities", []):
            ce_id = ent.get("config_entry_id")
            if ce_id:
                entry_to_entity[ce_id] = ent.get("entity_id")

        def normalize_for_matching(text):
            """Normalize text for diacritic-insensitive matching."""
            if not text:
                return ""
            import unicodedata

            text = unicodedata.normalize("NFKD", text.lower())
            text = "".join(c for c in text if not unicodedata.combining(c))
            return re.sub(r"[^a-z0-9]+", "_", text).strip("_")

        for entry in data:
            if entry.get("domain") != "template":
                continue

            opts = entry.get("options", {})
            entry_id = entry.get("entry_id")
            name = entry.get("title") or opts.get("name", "unknown")
            ttype = opts.get("template_type", "sensor")

            # Use authoritative entity_id from registry; fallback to normalized slug
            eid = entry_to_entity.get(entry_id)
            if not eid:
                eid = f"{ttype}.{normalize_for_matching(name)}"

            # Match: exact entity_id OR normalized diacritic-insensitive match
            if entity_id:
                if eid != entity_id and normalize_for_matching(name) != normalize_for_matching(
                    entity_id.split(".", 1)[-1] if "." in entity_id else entity_id
                ):
                    continue

            templates.append(
                {
                    "name": name,
                    "entity_id": eid,
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
            {"success": True, "total_templates": len(templates), "templates": templates},
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def get_template_entity_code(entity_id: str) -> str:
        """
        Returns full Jinja2 template code for a single template helper entity.
        ~95% token savings vs get_template_entities().

        Args:
            entity_id: Entity id of the template (e.g. "sensor.my_template").

        Returns:
            JSON with template metadata and full Jinja2 code.
        """
        if not entity_id or not isinstance(entity_id, str) or not entity_id.strip():
            return json.dumps(
                {"success": False, "error": "entity_id is required and must be a non-empty string"},
                indent=2,
            )

        data = load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])

        # Build entity_id lookup from entity registry for accurate matching
        entity_reg = load_registry("core.entity_registry", config_path)
        entry_to_entity = {}
        for ent in entity_reg.get("data", {}).get("entities", []):
            ce_id = ent.get("config_entry_id")
            if ce_id:
                entry_to_entity[ce_id] = ent.get("entity_id")

        import unicodedata

        def normalize_name(text):
            text = unicodedata.normalize("NFKD", text.lower())
            text = "".join(c for c in text if not unicodedata.combining(c))
            return re.sub(r"[^a-z0-9]+", "_", text).strip("_")

        for entry in data:
            if entry.get("domain") != "template":
                continue

            opts = entry.get("options", {})
            entry_id = entry.get("entry_id")
            name = entry.get("title") or opts.get("name", "unknown")
            ttype = opts.get("template_type", "sensor")

            # Use authoritative entity_id from registry; fallback to normalized slug
            eid = entry_to_entity.get(entry_id)
            if not eid:
                eid = f"{ttype}.{normalize_name(name)}"

            # Match: exact entity_id OR normalized diacritic-insensitive match
            if eid != entity_id and normalize_name(name) != normalize_name(
                entity_id.split(".", 1)[-1] if "." in entity_id else entity_id
            ):
                continue

            return json.dumps(
                {
                    "success": True,
                    "entity_id": entity_id,
                    "name": name,
                    "template_type": ttype,
                    "state_template": opts.get("state", ""),
                    "unit_of_measurement": opts.get("unit_of_measurement"),
                    "device_class": opts.get("device_class"),
                    "availability_template": opts.get("availability"),
                    "attribute_templates": opts.get("attributes", {}),
                    "entry_id": entry.get("entry_id"),
                    "created_at": entry.get("created_at"),
                    "modified_at": entry.get("modified_at"),
                },
                indent=2,
                ensure_ascii=False,
            )

        return json.dumps(
            {"success": False, "error": f"Template '{entity_id}' not found"},
            indent=2,
        )

    # ========================================
    # 🔄 COMPATIBILITY WRAPPERS
    # ========================================

    @mcp.tool()
    async def search_entity_by_name(search_term: str) -> str:
        """
        Searches entities by name or entity_id in the registry.

        Warning: Use search_registries_batch() for more advanced searching.

        Args:
            search_term: Phrase to search for
        """
        return await search_registries_batch(search_term=search_term)

    @mcp.tool()
    async def get_entity_details(entity_id: str) -> str:
        """
        Fetches detailed information about a specific entity from the registry.
        Contains related device and area.

        Warning: Use get_entity_context() for full context with live state.

        Args:
            entity_id: Entity id (e.g. "sensor.temperature_living_room")
        """
        return await get_entity_context(entity_id)

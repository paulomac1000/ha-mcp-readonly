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
import logging
import os
import re
import statistics
import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from tools.manifests import make_manifest, register_manifest
from tools.utils import (
    _error_response,
    _success_response,
    create_error_response,
    get_best_name,
    load_registry,
    make_ha_request,
    resolve_area_id,
)
from tools.yaml_utils import HomeAssistantLoader

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


# ========================================
# HELPER FUNCTIONS (module level)
# ========================================


def _resolve_dashboard_registry(dashboard: str, config_path: str) -> str:
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


def _get_dashboards_registry(config_path: str) -> list[Any]:
    """Load dashboards registry and return items list."""
    return load_registry("lovelace_dashboards", config_path).get("data", {}).get("items", [])  # type: ignore[no-any-return]


def _get_lovelace_cards(dashboard_id: str, config_path: str) -> list[Any]:
    """Extract flat list of card dicts from a dashboard config, with position info."""
    registry_name = _resolve_dashboard_registry(dashboard_id, config_path)
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


def _normalize_text(text: str) -> str:
    """Normalize text for diacritic-insensitive matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


# ========================================
# _do_* FUNCTIONS (module level)
# ========================================


def _do_search_registries_batch(
    search_term: str | None,
    entity_ids: str | None,
    area_id: str | None,
    device_id: str | None,
    platform: str | None,
    include_states: bool,
    config_path: str,
    ha_url: str | None,
    ha_token: str | None,
) -> dict[str, Any]:
    ent_data = (
        load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
    )
    dev_data = load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
    area_data = load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])

    dev_map = {d["id"]: d for d in dev_data}
    area_map = {a["id"]: a for a in area_data}

    target_eids = set(e.strip() for e in entity_ids.split(",")) if entity_ids else None
    term_lower = search_term.lower() if search_term else None

    matched_entities = []

    for entity in ent_data:
        eid = entity.get("entity_id")

        if target_eids and eid not in target_eids:
            continue

        if platform and entity.get("platform") != platform:
            continue

        final_area_id = resolve_area_id(entity, dev_map)

        if area_id and final_area_id != area_id:
            continue

        if device_id and entity.get("device_id") != device_id:
            continue

        if term_lower:
            name = get_best_name(entity, "entity").lower()
            if term_lower not in eid.lower() and term_lower not in name:
                continue

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

        did = entity.get("device_id")
        if did and did in dev_map:
            device = dev_map[did]
            entity_info["device"] = {
                "name": get_best_name(device, "device"),
                "manufacturer": device.get("manufacturer"),
                "model": device.get("model"),
            }

        if final_area_id and final_area_id in area_map:
            area = area_map[final_area_id]
            entity_info["area"] = {
                "name": area.get("name"),
                "id": area.get("id"),
            }

        matched_entities.append(entity_info)

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

    matched_areas = []
    if term_lower:
        for area in area_data:
            name = (area.get("name") or "").lower()
            if term_lower in name:
                matched_areas.append(area)

    matched_entities = matched_entities[:100]
    matched_devices = matched_devices[:50]
    matched_areas = matched_areas[:20]

    return {
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
    }


def _do_get_entity_context(
    entity_id: str,
    config_path: str,
    ha_url: str | None,
    ha_token: str | None,
) -> dict[str, Any]:
    ent_data = (
        load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
    )
    dev_data = load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
    area_data = load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
    config_data = (
        load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])
    )

    entity = next((e for e in ent_data if e.get("entity_id") == entity_id), None)

    if not entity:
        return {"success": False, "error": f"Entity '{entity_id}' not found in registry"}

    result = {  # type: ignore[var-annotated]
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

            state_val = s.get("state")
            if state_val == "unavailable":
                result["issues"].append("Entity is UNAVAILABLE")  # type: ignore[union-attr]
                result["recommendations"].append("Check device connection and integration status")  # type: ignore[union-attr]
            elif state_val == "unknown":
                result["issues"].append("Entity state is UNKNOWN")  # type: ignore[union-attr]
                result["recommendations"].append("Entity may not have reported state yet")  # type: ignore[union-attr]
        else:
            result["issues"].append(f"Could not fetch live state: {state_res.get('error')}")  # type: ignore[union-attr]

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

            for other in ent_data:
                if other.get("device_id") == did and other.get("entity_id") != entity_id:
                    result["related_entities"].append(  # type: ignore[union-attr]
                        {
                            "entity_id": other.get("entity_id"),
                            "platform": other.get("platform"),
                            "name": get_best_name(other, "entity"),
                            "disabled": other.get("disabled_by") is not None,
                        }
                    )

    dev_map = {d["id"]: d for d in dev_data}
    final_area_id = resolve_area_id(entity, dev_map)

    if final_area_id:
        area = next((a for a in area_data if a.get("id") == final_area_id), None)
        if area:
            result["area_info"] = area

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
                result["area_entities"].append(  # type: ignore[union-attr]
                    {"note": f"... and {len(area_entities_list) - 10} more entities in this area"}
                )

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

    if entity.get("disabled_by"):
        result["issues"].append(f"Entity is DISABLED by: {entity.get('disabled_by')}")  # type: ignore[union-attr]
        result["recommendations"].append("Enable entity in entity registry if needed")  # type: ignore[union-attr]

    if entity.get("hidden_by"):
        result["issues"].append(f"Entity is HIDDEN by: {entity.get('hidden_by')}")  # type: ignore[union-attr]

    if not result["issues"]:
        result["recommendations"].append("Entity appears to be configured correctly")  # type: ignore[union-attr]

    return result


def _do_get_area_overview(
    area_id: str,
    config_path: str,
    ha_url: str | None,
    ha_token: str | None,
) -> dict[str, Any]:
    area_data = load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
    dev_data = load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
    ent_data = (
        load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
    )

    area = next(
        (
            a
            for a in area_data
            if a.get("id") == area_id or a.get("name", "").lower() == area_id.lower()
        ),
        None,
    )

    if not area:
        return {"success": False, "error": f"Area '{area_id}' not found"}

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

    area_devices = [d["id"] for d in dev_data if d.get("area_id") == final_area_id]
    result["devices_count"] = len(area_devices)

    dev_map = {d["id"]: d for d in dev_data}
    area_entities = []
    for e in ent_data:
        resolved_area = resolve_area_id(e, dev_map)
        if resolved_area == final_area_id:
            area_entities.append(e)

    for entity in area_entities:
        domain = entity.get("entity_id", "").split(".")[0]
        result["entities_by_domain"][domain] = result["entities_by_domain"].get(domain, 0) + 1

    if ha_url and ha_token and area_entities:
        states_res = make_ha_request(ha_url, ha_token, "/api/states")
        if states_res["success"]:
            states_map = {s["entity_id"]: s for s in states_res["data"]}

            for entity in area_entities[:50]:
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

                if state_val == "unavailable":
                    result["unavailable_entities"].append(eid)
                    result["issues"].append(f"{name} ({eid}) is unavailable")
                elif state_val == "unknown":
                    result["issues"].append(f"{name} ({eid}) has unknown state")

                if state_val not in ["unavailable", "unknown", "on", "off", ""]:
                    try:
                        float(state_val)
                        unit = state_data.get("attributes", {}).get("unit_of_measurement", "")
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

    if result["devices_count"] == 0 and len(area_entities) > 0:
        result["issues"].append("Area has entities but no devices assigned directly")

    return result


def _do_get_history_stats(
    entity_id: str,
    hours_back: int,
    ha_url: str | None,
    ha_token: str | None,
) -> dict[str, Any]:
    if not ha_url or not ha_token:
        return {"success": False, "error": "HA API not configured"}

    hours_back = min(hours_back, 168)

    start_time = datetime.now(UTC) - timedelta(hours=hours_back)
    url = f"/api/history/period/{start_time.isoformat()}?filter_entity_id={entity_id}&minimal_response=true"

    res = make_ha_request(ha_url, ha_token, url)
    if not res["success"] or not res["data"] or not res["data"][0]:
        return {"success": False, "error": "No history data found"}

    history = res["data"][0]
    states = [h["state"] for h in history if h["state"] not in ["unavailable", "unknown"]]

    if not states:
        return {"info": "No valid states in period"}

    try:
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
        counts = Counter(states)
        total = len(states)
        analysis = {
            "type": "categorical",
            "most_common": counts.most_common(1)[0][0],
            "distribution": {
                k: {"count": v, "percentage": round(v / total * 100, 1)} for k, v in counts.items()
            },
            "samples": total,
            "period_hours": hours_back,
        }

    return {"entity_id": entity_id, "analysis": analysis}


def _do_get_entity_registry(config_path: str) -> dict[str, Any]:
    data = load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
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
                "categories": e.get("categories"),
                "labels": e.get("labels"),
            }
        )
    return {"total_entities": len(simplified), "entities": simplified}


def _do_get_entity_registry_batch(
    config_path: str, entity_ids: str | None = None, fields: str = "all"
) -> dict[str, Any]:
    """Fetch entity registry entries, optionally filtered by entity IDs and fields.

    Args:
        config_path: Path to HA config directory.
        entity_ids: Comma-separated list of entity IDs, or None for all.
        fields: Comma-separated list of field names, or "all".

    Returns:
        Dict with total_entities and filtered entities list.
    """
    data = load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
    all_fields = {
        "entity_id", "name", "platform", "device_id", "area_id",
        "disabled_by", "hidden_by", "unique_id", "config_entry_id",
        "categories", "labels",
    }
    if fields != "all":
        requested = {f.strip() for f in fields.split(",")}
        selected = (requested | {"categories", "labels", "entity_id"}) & all_fields
    else:
        selected = all_fields

    requested_ids = set()
    if entity_ids:
        requested_ids = {eid.strip() for eid in entity_ids.split(",")}

    simplified = []
    for e in data:
        eid = e.get("entity_id")
        if requested_ids and eid not in requested_ids:
            continue
        entry: dict[str, Any] = {"entity_id": eid}
        if "name" in selected:
            entry["name"] = get_best_name(e, "entity")
        if "platform" in selected:
            entry["platform"] = e.get("platform")
        if "device_id" in selected:
            entry["device_id"] = e.get("device_id")
        if "area_id" in selected:
            entry["area_id"] = e.get("area_id")
        if "disabled_by" in selected:
            entry["disabled_by"] = e.get("disabled_by")
        if "hidden_by" in selected:
            entry["hidden_by"] = e.get("hidden_by")
        if "unique_id" in selected:
            entry["unique_id"] = e.get("unique_id")
        if "config_entry_id" in selected:
            entry["config_entry_id"] = e.get("config_entry_id")
        if "categories" in selected:
            entry["categories"] = e.get("categories")
        if "labels" in selected:
            entry["labels"] = e.get("labels")
        simplified.append(entry)

    return {"total_entities": len(simplified), "entities": simplified}


def _do_get_device_registry(config_path: str) -> dict[str, Any]:
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
    return {"total_devices": len(simplified), "devices": simplified}


def _do_get_area_registry(config_path: str) -> dict[str, Any]:
    data = load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
    return {"total_areas": len(data), "areas": data}


def _do_get_config_entries(config_path: str) -> dict[str, Any]:
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
    return {"total_entries": len(simplified), "entries": simplified}


def _do_get_lovelace_dashboards(config_path: str) -> dict[str, Any]:
    data = load_registry("lovelace_dashboards", config_path).get("data", {}).get("items", [])
    return {"total_dashboards": len(data), "dashboards": data}


def _do_get_lovelace_config(dashboard: str, config_path: str) -> dict[str, Any]:
    registry_name = _resolve_dashboard_registry(dashboard, config_path)
    data = load_registry(registry_name, config_path)
    if not data:
        return {"error": f"Dashboard '{dashboard}' not found"}
    return data


def _do_get_lovelace_resources(config_path: str) -> dict[str, Any]:
    data = load_registry("lovelace_resources", config_path).get("data", {}).get("items", [])
    by_type = {}  # type: ignore[var-annotated]
    for r in data:
        rtype = r.get("type", "unknown")
        by_type[rtype] = by_type.get(rtype, 0) + 1
    return {
        "total_resources": len(data),
        "resources": data,
        "by_type": by_type,
    }


def _do_search_lovelace_config(
    search_term: str | None,
    card_type: str | None,
    entity_id: str | None,
    dashboard: str | None,
    max_results: int,
    config_path: str,
) -> dict[str, Any]:
    matches = []  # type: ignore[var-annotated]
    dashboards_data = _get_dashboards_registry(config_path)

    target_ids = []
    if dashboard:
        for d in dashboards_data:
            if d.get("url_path") == dashboard or d.get("id") == dashboard:
                target_ids.append(d.get("id", ""))
        if not target_ids:
            return {
                "matched_count": 0,
                "matches": [],
                "warnings": [f"Dashboard '{dashboard}' not found"],
            }
    else:
        target_ids = [d.get("id", "") for d in dashboards_data]

    dashboards_scanned = 0
    warnings = []

    for dash_id in target_ids:
        if not dash_id:
            continue
        try:
            cards = _get_lovelace_cards(dash_id, config_path)
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
                        if e == entity_id or (isinstance(e, dict) and e.get("entity") == entity_id):
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

    return {
        "matched_count": len(matches),
        "dashboards_scanned": dashboards_scanned,
        "dashboards_skipped": len(target_ids) - dashboards_scanned,
        "matches": matches,
        "warnings": warnings if warnings else None,
    }


def _do_get_lovelace_config_summary(dashboard: str | None, config_path: str) -> dict[str, Any]:
    dashboards_data = _get_dashboards_registry(config_path)

    if dashboard:
        target = None
        for d in dashboards_data:
            if d.get("url_path") == dashboard or d.get("id") == dashboard:
                target = d
                break
        if not target:
            return {"error": f"Dashboard '{dashboard}' not found"}
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
            cards = _get_lovelace_cards(dash_id, config_path)
        except Exception:
            info["error"] = "Could not load dashboard config"
            summaries.append(info)
            continue

        if not cards:
            config = load_registry(_resolve_dashboard_registry(dash_id, config_path), config_path)
            strategy = config.get("data", {}).get("config", {}).get("strategy", {})
            if strategy:
                info["strategy"] = strategy.get("type", "unknown")
            summaries.append(info)
            continue

        views_map = {}
        badges_count = 0
        card_types = {}  # type: ignore[var-annotated]
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
        return {"dashboard": summaries[0]}

    total_views = sum(s["total_views"] for s in summaries if "total_views" in s)
    total_cards = sum(s["total_cards"] for s in summaries if "total_cards" in s)
    strategy_dashboards = [s["id"] for s in summaries if s.get("strategy")]
    yaml_dashboards = [s["id"] for s in summaries if s.get("mode") == "yaml"]

    global_ct = {}  # type: ignore[var-annotated]
    for s in summaries:
        for ct, cnt in s.get("card_types_breakdown", {}).items():
            global_ct[ct] = global_ct.get(ct, 0) + cnt

    return {
        "total_dashboards": len(summaries),
        "dashboards_summary": summaries,
        "global_stats": {
            "total_views": total_views,
            "total_cards": total_cards,
            "top_card_types": dict(sorted(global_ct.items(), key=lambda x: -x[1])[:10]),
            "strategy_dashboards": strategy_dashboards,
            "yaml_mode_dashboards": yaml_dashboards,
        },
    }


def _do_diagnose_lovelace_setup(config_path: str) -> dict[str, Any]:
    dashboards_data = _get_dashboards_registry(config_path)
    resources_data = (
        load_registry("lovelace_resources", config_path).get("data", {}).get("items", [])
    )

    entity_registry = (
        load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
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
            cards = _get_lovelace_cards(dash_id, config_path)
        except Exception:
            dashboard_stats.append(info)
            continue

        if not cards:
            config = load_registry(_resolve_dashboard_registry(dash_id, config_path), config_path)
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
        unique_missing = {}  # type: ignore[var-annotated]
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
                f"Remove or replace references to '{eid}' in dashboards: {', '.join(dashbs)}"
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

    return {
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
    }


def _do_get_exposed_entities(config_path: str) -> dict[str, Any]:
    data = load_registry("cloud.google_assistant", config_path).get("data", {}).get("entities", {})
    return {"total_exposed": len(data), "entities": data}


def _do_get_persons(config_path: str) -> dict[str, Any]:
    data = load_registry("person", config_path).get("data", {}).get("items", [])
    return {"total_persons": len(data), "persons": data}


def _do_get_zones(config_path: str) -> dict[str, Any]:
    data = load_registry("zone", config_path).get("data", {}).get("items", [])
    return {"total_zones": len(data), "zones": data}


def _do_get_input_helpers(config_path: str) -> dict[str, Any]:
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

    return helpers


def _do_get_hacs_data(config_path: str) -> dict[str, Any]:
    data = load_registry("hacs.data", config_path)
    if not data:
        return {"info": "HACS not installed or storage file not found"}
    return data


def _do_hacs_get_update_count(config_path: str) -> dict[str, Any]:
    data = load_registry("hacs.data", config_path)
    if not data:
        return {"error": "HACS not installed or storage file not found"}

    repos = data.get("data", {}).get("repositories", [])
    total_installed = 0
    updates_available = 0
    critical_updates: list[dict[str, Any]] = []
    major_version_bumps: list[dict[str, Any]] = []

    for repo in repos:
        installed = repo.get("installed", False)
        if not installed:
            continue
        total_installed += 1

        available = repo.get("available_updates", 0)
        if available and int(available) > 0:
            updates_available += 1

            name = repo.get("full_name", repo.get("name", "unknown"))
            installed_version = repo.get("installed_version", "")
            available_version = repo.get("available_version", "")

            if installed_version and available_version:
                iv_parts = installed_version.lstrip("v").split(".")
                av_parts = available_version.lstrip("v").split(".")
                if iv_parts and av_parts and iv_parts[0] != av_parts[0]:
                    major_version_bumps.append(
                        {
                            "name": name,
                            "installed": installed_version,
                            "available": available_version,
                        }
                    )

            if repo.get("critical", False):
                critical_updates.append(
                    {
                        "name": name,
                        "installed": installed_version,
                        "available": available_version,
                    }
                )

    return {
        "total_installed": total_installed,
        "updates_available": updates_available,
        "critical_updates": critical_updates,
        "major_version_bumps": major_version_bumps,
    }


def _do_get_nfc_tags(config_path: str, ha_url: str | None, ha_token: str | None) -> dict[str, Any]:
    tags: list[dict[str, Any]] = []

    tag_registry = load_registry("core.tag", config_path)
    if tag_registry.get("data", {}).get("tags"):
        for t in tag_registry["data"]["tags"]:
            tags.append(
                {
                    "tag_id": t.get("id"),
                    "name": t.get("name"),
                    "last_scanned": t.get("last_scanned"),
                }
            )

    if not tags and ha_url and ha_token:
        states_data = make_ha_request(ha_url, ha_token, "/api/states")
        if states_data.get("success"):
            for s in states_data["data"]:
                if s.get("entity_id", "").startswith("tag."):
                    tags.append(
                        {
                            "tag_id": s["entity_id"],
                            "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                            "last_scanned": s.get("attributes", {}).get("last_scanned"),
                        }
                    )

    if not tags:
        return {"tags": [], "total": 0, "unused_count": 0, "info": "No NFC tags found"}

    automations_path = Path(config_path) / "automations.yaml"
    automations_text = ""
    if automations_path.exists():
        automations_text = automations_path.read_text(encoding="utf-8")

    for tag in tags:
        tag_id = tag["tag_id"]
        used_in = []
        if tag_id and automations_text and tag_id in automations_text:
            used_in.append(tag_id)
        tag["used_in_automations"] = used_in

    unused_count = sum(1 for t in tags if not t.get("used_in_automations"))

    return {
        "tags": tags,
        "total": len(tags),
        "unused_count": unused_count,
    }


def _do_get_timers(config_path: str) -> dict[str, Any]:
    data = load_registry("timer", config_path).get("data", {}).get("items", [])
    return {"total_timers": len(data), "timers": data}


def _do_get_counters(config_path: str) -> dict[str, Any]:
    data = load_registry("counter", config_path).get("data", {}).get("items", [])
    return {"total_counters": len(data), "counters": data}


def _do_get_template_entities(entity_id: str | None, config_path: str) -> dict[str, Any]:
    data = load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])

    entity_reg = load_registry("core.entity_registry", config_path)
    entry_to_entity = {}
    for ent in entity_reg.get("data", {}).get("entities", []):
        ce_id = ent.get("config_entry_id")
        if ce_id:
            entry_to_entity[ce_id] = ent.get("entity_id")

    templates = []

    for entry in data:
        if entry.get("domain") != "template":
            continue

        opts = entry.get("options", {})
        entry_id = entry.get("entry_id")
        name = entry.get("title") or opts.get("name", "unknown")
        ttype = opts.get("template_type", "sensor")

        eid = entry_to_entity.get(entry_id)
        if not eid:
            eid = f"{ttype}.{_normalize_text(name)}"

        if entity_id:
            if eid != entity_id and _normalize_text(name) != _normalize_text(
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

    return {"total_templates": len(templates), "templates": templates}


def _do_get_template_entity_code(
    entity_id: str, config_path: str, force_reload: bool = False
) -> dict[str, Any]:
    if not entity_id or not isinstance(entity_id, str) or not entity_id.strip():
        return {"error": "entity_id is required and must be a non-empty string"}

    if force_reload:
        from tools.utils import invalidate_registry_cache

        invalidate_registry_cache("core.config_entries", config_path)

    data = load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])

    entity_reg = load_registry("core.entity_registry", config_path)
    entry_to_entity = {}
    for ent in entity_reg.get("data", {}).get("entities", []):
        ce_id = ent.get("config_entry_id")
        if ce_id:
            entry_to_entity[ce_id] = ent.get("entity_id")

    for entry in data:
        if entry.get("domain") != "template":
            continue

        opts = entry.get("options", {})
        entry_id = entry.get("entry_id")
        name = entry.get("title") or opts.get("name", "unknown")
        ttype = opts.get("template_type", "sensor")

        eid = entry_to_entity.get(entry_id)
        if not eid:
            eid = f"{ttype}.{_normalize_text(name)}"

        if eid != entity_id and _normalize_text(name) != _normalize_text(
            entity_id.split(".", 1)[-1] if "." in entity_id else entity_id
        ):
            continue

        return {
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
            "source": "live" if force_reload else "cache",
        }

    yaml_result = _scan_yaml_templates(entity_id, config_path)
    if yaml_result:
        return yaml_result

    return create_error_response(
        "NOT_FOUND",
        f"Template '{entity_id}' not found",
        retryable=False,
        suggestion="Check the entity_id or verify the template is defined in configuration.yaml or the templates/ directory.",
    )


def _scan_yaml_templates(entity_id: str, config_path: str) -> dict[str, Any] | None:
    """Scan YAML configuration files for template sensor/binary_sensor definitions.

    Searches configuration.yaml ``template:`` section and individual ``templates/*.yaml``
    files. Handles ``!include_dir_merge_list`` directives and inline template lists.
    Matches by ``unique_id`` or normalized ``name``.

    Args:
        entity_id: Full entity_id (e.g. ``sensor.my_template``).
        config_path: Path to HA config directory.

    Returns:
        Dict with template info (source="yaml") or None if not found.
    """
    if "." not in entity_id:
        return None

    domain, obj_id = entity_id.split(".", 1)
    if domain not in ("sensor", "binary_sensor"):
        return None

    norm_obj_id = _normalize_text(obj_id)

    config_yaml = os.path.join(config_path, "configuration.yaml")
    if os.path.isfile(config_yaml) and not os.path.islink(config_yaml):
        result = _scan_yaml_template_payload(
            config_yaml, domain, obj_id, norm_obj_id, config_path
        )
        if result:
            return result

    templates_dir = os.path.join(config_path, "templates")
    if os.path.isdir(templates_dir) and not os.path.islink(templates_dir):
        result = _scan_yaml_template_directory(
            templates_dir, domain, obj_id, norm_obj_id, config_path
        )
        if result:
            return result

    return None


def _scan_yaml_template_file(
    filepath: str,
    domain: str,
    obj_id: str,
    norm_obj_id: str,
    config_path: str,
) -> dict[str, Any] | None:
    """Load and scan a single YAML file for template definitions.

    The file may contain a top-level ``template:`` section.
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            data = yaml.load(f, Loader=HomeAssistantLoader) or {}  # nosec B506
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    template_section = data.get("template")
    if not template_section:
        return None

    result = _scan_yaml_template_section(
        template_section, filepath, domain, obj_id, norm_obj_id, config_path
    )
    if result:
        return result

    return None


def _scan_yaml_template_payload(
    filepath: str,
    domain: str,
    obj_id: str,
    norm_obj_id: str,
    config_path: str,
) -> dict[str, Any] | None:
    """Load a YAML file and scan entire payload for template definitions.

    Handles cases where the file IS the template payload (e.g. templates/*.yaml
    files that contain sensor definitions directly, without a top-level ``template:`` key).
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            data = yaml.load(f, Loader=HomeAssistantLoader) or {}  # nosec B506
    except Exception:
        return None

    if isinstance(data, dict) and "template" in data:
        return _scan_yaml_template_file(
            filepath, domain, obj_id, norm_obj_id, config_path
        )

    for sensor_def in _iter_sensor_definitions(data, domain):
        if _sensor_matches(sensor_def, obj_id, norm_obj_id):
            return _build_yaml_template_result(
                sensor_def, domain, obj_id, filepath, config_path
            )

    return None


def _scan_yaml_template_directory(
    templates_dir: str,
    domain: str,
    obj_id: str,
    norm_obj_id: str,
    config_path: str,
) -> dict[str, Any] | None:
    """Scan all .yaml/.yml files in a templates directory.

    Safety limits: max 100 files, no symlink following.
    """
    scanned = 0
    for fname in sorted(os.listdir(templates_dir)):
        if not fname.endswith((".yaml", ".yml")):
            continue
        fpath = os.path.join(templates_dir, fname)
        if os.path.islink(fpath) or not os.path.isfile(fpath):
            continue
        scanned += 1
        if scanned > 100:
            break
        result = _scan_yaml_template_payload(
            fpath, domain, obj_id, norm_obj_id, config_path
        )
        if result:
            return result
    return None


def _scan_yaml_template_section(
    template_section: Any,
    filepath: str,
    domain: str,
    obj_id: str,
    norm_obj_id: str,
    config_path: str,
) -> dict[str, Any] | None:
    """Process a ``template:`` section value from a YAML file.

    Handles:
    - Inline list of template entries
    - ``!include_dir_merge_list`` directives
    - ``!include`` directives within list entries
    """
    if isinstance(template_section, str):
        return _resolve_include_string(
            template_section, domain, obj_id, norm_obj_id, config_path
        )

    if not isinstance(template_section, list):
        return None

    for entry in template_section:
        if isinstance(entry, str):
            result = _resolve_include_string(
                entry, domain, obj_id, norm_obj_id, config_path
            )
            if result:
                return result
            continue

        if not isinstance(entry, dict):
            continue

        sensors = entry.get(domain)
        if sensors is None:
            continue

        if isinstance(sensors, dict):
            sensors = [sensors]
        if not isinstance(sensors, list):
            continue

        for sensor_def in sensors:
            if not isinstance(sensor_def, dict):
                continue
            if _sensor_matches(sensor_def, obj_id, norm_obj_id):
                return _build_yaml_template_result(
                    sensor_def, domain, obj_id, filepath, config_path
                )

    return None


def _resolve_include_string(
    include_str: str,
    domain: str,
    obj_id: str,
    norm_obj_id: str,
    config_path: str,
) -> dict[str, Any] | None:
    """Resolve ``!include`` / ``!include_dir_merge_list`` directives."""
    match = re.match(r"!(?:include|include_dir_merge_list)\s+(.+)", include_str.strip())
    if not match:
        return None

    target = match.group(1).strip()
    if not os.path.isabs(target):
        target = os.path.join(config_path, target)

    if os.path.isdir(target) and not os.path.islink(target):
        return _scan_yaml_template_directory(
            target, domain, obj_id, norm_obj_id, config_path
        )

    if os.path.isfile(target) and not os.path.islink(target):
        return _scan_yaml_template_payload(
            target, domain, obj_id, norm_obj_id, config_path
        )

    return None


def _iter_sensor_definitions(data: Any, domain: str):
    """Yield sensor definitions from various YAML structures."""
    if isinstance(data, dict):
        sensors = data.get(domain)
        if sensors is None and domain == "sensor":
            sensors = data.get("sensor")
        if sensors is None and domain == "binary_sensor":
            sensors = data.get("binary_sensor")
        if sensors is None:
            return
        if isinstance(sensors, dict):
            yield sensors
        elif isinstance(sensors, list):
            yield from sensors
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                sd = item.get(domain)
                if sd is not None:
                    if isinstance(sd, dict):
                        yield sd
                    elif isinstance(sd, list):
                        yield from sd


def _sensor_matches(
    sensor_def: dict[str, Any],
    obj_id: str,
    norm_obj_id: str,
) -> bool:
    """Check if a sensor definition matches the requested entity."""
    uid = sensor_def.get("unique_id")
    if uid and str(uid) == obj_id:
        return True
    name = sensor_def.get("name")
    if name and _normalize_text(str(name)) == norm_obj_id:
        return True
    return False


def _find_yaml_line_boundaries(
    filepath: str, sensor_def: dict[str, Any]
) -> tuple[int | None, int | None]:
    """Locate approximate line boundaries of a sensor definition in a YAML file.

    Searches for the ``unique_id`` or ``name`` value in the raw file lines,
    then expands backward/forward to capture the full sensor block at the
    same indentation level.

    Returns:
        (line_start, line_end) — 1-indexed, or (None, None) on failure.
    """
    lookup_value = sensor_def.get("unique_id") or sensor_def.get("name")
    if not lookup_value:
        return None, None
    lookup_str = str(lookup_value)

    try:
        with open(filepath, encoding="utf-8") as f:
            raw_lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return None, None

    if not raw_lines:
        return None, None

    found_line = None
    for i, line in enumerate(raw_lines):
        if lookup_str in line:
            found_line = i
            break
    if found_line is None:
        return None, None

    line_indent = len(raw_lines[found_line]) - len(raw_lines[found_line].lstrip())

    line_start = found_line
    for i in range(found_line - 1, -1, -1):
        stripped = raw_lines[i].rstrip()
        indent = len(raw_lines[i]) - len(raw_lines[i].lstrip())
        if indent < line_indent and stripped:
            line_start = i
            break

    line_end = found_line
    for i in range(found_line + 1, len(raw_lines)):
        stripped = raw_lines[i].rstrip()
        if not stripped:
            continue
        indent = len(raw_lines[i]) - len(raw_lines[i].lstrip())
        if indent <= line_indent:
            line_end = i - 1
            break
    else:
        line_end = len(raw_lines) - 1

    return line_start + 1, line_end + 1


def _build_yaml_template_result(
    sensor_def: dict[str, Any],
    domain: str,
    obj_id: str,
    filepath: str,
    config_path: str,
) -> dict[str, Any]:
    """Build template result dict from a matched YAML sensor definition."""
    rel_path = (
        os.path.relpath(filepath, config_path) if config_path else filepath
    )

    line_start, line_end = _find_yaml_line_boundaries(filepath, sensor_def)

    return {
        "entity_id": f"{domain}.{obj_id}",
        "name": sensor_def.get("name", sensor_def.get("unique_id", obj_id)),
        "template_type": domain,
        "state_template": sensor_def.get("state", ""),
        "unit_of_measurement": sensor_def.get("unit_of_measurement"),
        "device_class": sensor_def.get("device_class"),
        "availability_template": sensor_def.get("availability"),
        "attribute_templates": sensor_def.get("attributes", {}),
        "icon": sensor_def.get("icon"),
        "source": "yaml",
        "file_path": rel_path,
        "line_start": line_start,
        "line_end": line_end,
    }


def _do_get_template_entities_batch(
    entity_ids: str, config_path: str
) -> dict[str, Any]:
    """Batch fetch template entity code for multiple entities.

    Args:
        entity_ids: Comma-separated list of entity IDs.
        config_path: Path to HA config directory.

    Returns:
        Dict keyed by entity_id with template code or error per entity.
    """
    ids_list = [eid.strip() for eid in entity_ids.split(",") if eid.strip()]
    if not ids_list:
        return {"success": False, "error": "No entity_ids provided"}

    results: dict[str, Any] = {}
    errors_count = 0
    found_count = 0

    for entity_id in ids_list:
        result = _do_get_template_entity_code(entity_id=entity_id, config_path=config_path)
        results[entity_id] = result
        if "error" in result:
            errors_count += 1
        else:
            found_count += 1

    return {
        "total": len(ids_list),
        "found": found_count,
        "errors": errors_count,
        "results": results,
    }


def _do_search_entity_by_name(
    search_term: str,
    config_path: str,
    ha_url: str | None,
    ha_token: str | None,
) -> dict[str, Any]:
    return _do_search_registries_batch(
        search_term=search_term,
        entity_ids=None,
        area_id=None,
        device_id=None,
        platform=None,
        include_states=False,
        config_path=config_path,
        ha_url=ha_url,
        ha_token=ha_token,
    )


def _do_get_entity_details(
    entity_id: str,
    config_path: str,
    ha_url: str | None,
    ha_token: str | None,
) -> dict[str, Any]:
    return _do_get_entity_context(
        entity_id=entity_id,
        config_path=config_path,
        ha_url=ha_url,
        ha_token=ha_token,
    )


# ========================================
# REGISTRATION FUNCTION
# ========================================


def register_storage_tools(  # type: ignore[no-untyped-def]
    mcp, config_path: str, ha_url: str | None = None, ha_token: str | None = None
) -> None:
    """
    Registers tools for working with .storage files and hybrid diagnostic tools.
    """

    # ========================================
    # OPTIMIZED BATCH TOOLS
    # ========================================

    @mcp.tool()
    async def search_registries_batch(
        search_term: str | None = None,
        entity_ids: str | None = None,
        area_id: str | None = None,
        device_id: str | None = None,
        platform: str | None = None,
        include_states: bool = False,
    ) -> str:
        """[READ] Search multiple registries (entity, device, area) simultaneously by name, entity_ids, area, device, or platform. ~85% token savings.

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
            result = _do_search_registries_batch(
                search_term=search_term,
                entity_ids=entity_ids,
                area_id=area_id,
                device_id=device_id,
                platform=platform,
                include_states=include_states,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_entity_context(entity_id: str) -> str:
        """[READ] Get comprehensive entity context: state, registry info, related entities, automations, and device details. ~75% token savings.

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
            result = _do_get_entity_context(
                entity_id=entity_id,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_area_overview(area_id: str) -> str:
        """[READ] BATCH ENDPOINT - Comprehensive area overview.

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
            result = _do_get_area_overview(
                area_id=area_id,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_history_stats(entity_id: str, hours_back: int = 24) -> str:
        """[READ] Get entity history statistics: min, max, average values from the recorder. ~90% token savings vs raw history.

        Returns processed data (Min, Max, Average) instead of raw list.
        ~90% token savings when analyzing history.

        Args:
            entity_id: Entity id.
            hours_back: How many hours back to analyze (default: 24, max: 168).

        Returns:
            JSON with analysis (numeric or categorical).
        """
        try:
            result = _do_get_history_stats(
                entity_id=entity_id,
                hours_back=hours_back,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    # ========================================
    # REGISTRY DUMP TOOLS
    # ========================================

    @mcp.tool()
    async def get_entity_registry() -> str:
        """[READ] Fetches registry of all entities from .storage.
        Contains: entity_id, platform, device_id, aliases, disabled_by, hidden_by.

        Warning: returns all entities - use search_registries_batch() for filtering.
        """
        try:
            result = _do_get_entity_registry(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_device_registry() -> str:
        """[READ] Fetches registry of all devices from .storage.
        Contains: name, manufacturer, model, sw_version, connections, identifiers.
        """
        try:
            result = _do_get_device_registry(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_area_registry() -> str:
        """[READ] Fetches registry of all areas/rooms from .storage.
        Contains: name, aliases, picture, icon.
        """
        try:
            result = _do_get_area_registry(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_config_entries() -> str:
        """[READ] Fetches all configuration entries of integrations.
        Shows installed integrations, their domains, titles, versions, options.
        """
        try:
            result = _do_get_config_entries(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_lovelace_dashboards() -> str:
        """[READ] Fetches list of all Lovelace dashboards."""
        try:
            result = _do_get_lovelace_dashboards(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_lovelace_config(dashboard: str = "lovelace") -> str:
        """[READ] Fetches configuration of a specific Lovelace dashboard.

        Args:
            dashboard: Dashboard name (default: "lovelace" for main)
        """
        try:
            result = _do_get_lovelace_config(dashboard=dashboard, config_path=config_path)
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_lovelace_resources() -> str:
        """[READ] Fetches list of registered Lovelace resources (custom cards, JS modules, CSS).

        Returns:
            JSON with resource list, type breakdown, and source classification.
        """
        try:
            result = _do_get_lovelace_resources(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def search_lovelace_config(
        search_term: str | None = None,
        card_type: str | None = None,
        entity_id: str | None = None,
        dashboard: str | None = None,
        max_results: int = 50,
    ) -> str:
        """[READ] Search inside Lovelace dashboard configurations by entity_id, card_type, or free-text.

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
            result = _do_search_lovelace_config(
                search_term=search_term,
                card_type=card_type,
                entity_id=entity_id,
                dashboard=dashboard,
                max_results=max_results,
                config_path=config_path,
            )
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_lovelace_config_summary(dashboard: str | None = None) -> str:
        """[READ] Token-efficient Lovelace dashboard structure overview: card type breakdown, view counts, strategy detection. ~95% token savings.

        Returns card counts, type breakdown, view info. ~95% token savings
        vs returning full dashboard JSON.

        Args:
            dashboard: Specific dashboard url_path/id, or None for all dashboards.
        """
        try:
            result = _do_get_lovelace_config_summary(
                dashboard=dashboard,
                config_path=config_path,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def diagnose_lovelace_setup() -> str:
        """[READ] Full Lovelace diagnostics: missing entity references, strategy/YAML mode, resource analysis, recommendations. ~85% token savings.

        Checks: missing entity references, strategy/YAML mode dashboards,
        resource analysis, and generates recommendations. ~85% token savings
        vs manual multi-call workflow.
        """
        try:
            result = _do_diagnose_lovelace_setup(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_exposed_entities() -> str:
        """[READ] Fetches list of entities exposed to voice assistants (Google Assistant, Alexa)."""
        try:
            result = _do_get_exposed_entities(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_persons() -> str:
        """[READ] Fetches list of people (person entities) with their configuration."""
        try:
            result = _do_get_persons(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_zones() -> str:
        """[READ] Fetches list of zones with their configuration."""
        try:
            result = _do_get_zones(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_input_helpers() -> str:
        """[READ] Fetches all input helpers (input_boolean, input_number, input_select, input_text, input_datetime, input_button)."""
        try:
            result = _do_get_input_helpers(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_hacs_data() -> str:
        """[READ] Fetches HACS data (Home Assistant Community Store).
        Shows installed custom integrations and themes.
        """
        try:
            result = _do_get_hacs_data(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def hacs_get_update_count() -> str:
        """[READ] Get HACS repository update counts, critical updates, and major version bumps.

        Lightweight statistics from hacs.data — total installed, updates available,
        critical updates, and major version bumps (e.g. v1.x -> v2.x).

        Args:
            None

        Returns:
            JSON with total_installed, updates_available, critical_updates list,
            major_version_bumps list.
        """
        try:
            result = _do_hacs_get_update_count(config_path=config_path)
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_nfc_tags() -> str:
        """[READ] List NFC tags with automation usage info.

        Reads from .storage/core.tag registry, falls back to /api/states for tag.* entities.
        Cross-references each tag against automations.yaml to determine usage.

        Args:
            None

        Returns:
            JSON with tags list (tag_id, name, last_scanned, used_in_automations),
            total count, and unused_count.
        """
        try:
            result = _do_get_nfc_tags(
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_timers() -> str:
        """[READ] Fetches list of timers with their configuration."""
        try:
            result = _do_get_timers(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_counters() -> str:
        """[READ] Fetches list of counters with their configuration."""
        try:
            result = _do_get_counters(config_path=config_path)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_template_entities(entity_id: str | None = None) -> str:
        """[READ] Fetches template sensors and binary_sensors created via UI.
        Template helpers are stored in .storage/core.config_entries.

        Args:
            entity_id: Optional filter by entity_id (e.g. "sensor.my_template").
                       When provided, returns only the matching template.

        Returns:
            JSON with total_templates, templates list, and their configuration.
        """
        try:
            result = _do_get_template_entities(
                entity_id=entity_id,
                config_path=config_path,
            )
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_template_entity_code(entity_id: str, force_reload: bool = False) -> str:
        """[READ] Returns full Jinja2 template code for a single template helper entity.
        ~95% token savings vs get_template_entities().

        Args:
            entity_id: Entity id of the template (e.g. "sensor.my_template").
            force_reload: Read directly from storage instead of cache (default: False).

        Returns:
            JSON with template metadata, full Jinja2 code, and source indicator.
        """
        try:
            result = _do_get_template_entity_code(
                entity_id=entity_id,
                config_path=config_path,
                force_reload=force_reload,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    register_manifest(
        "get_template_entities_batch",
        make_manifest("get_template_entities_batch", latency="moderate"),
    )

    @mcp.tool()
    async def get_template_entities_batch(entity_ids: str) -> str:
        """[READ] Batch fetch template entity code for multiple entities.

        Args:
            entity_ids: Comma-separated list of entity IDs (e.g. "sensor.my_tpl1,sensor.my_tpl2").

        Returns:
            JSON with results dict keyed by entity_id, plus total/found/errors counts.
        """
        try:
            result = _do_get_template_entities_batch(
                entity_ids=entity_ids,
                config_path=config_path,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    # ========================================
    # COMPATIBILITY WRAPPERS
    # ========================================

    @mcp.tool()
    async def search_entity_by_name(search_term: str) -> str:
        """[READ] Searches entities by name or entity_id in the registry.

        Warning: Use search_registries_batch() for more advanced searching.

        Args:
            search_term: Phrase to search for
        """
        try:
            result = _do_search_entity_by_name(
                search_term=search_term,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_entity_details(entity_id: str) -> str:
        """[READ] Fetches detailed information about a specific entity from the registry.
        Contains related device and area.

        Warning: Use get_entity_context() for full context with live state.

        Args:
            entity_id: Entity id (e.g. "sensor.temperature_living_room")
        """
        try:
            result = _do_get_entity_details(
                entity_id=entity_id,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    # ========================================
    # ENTITY REGISTRY BATCH
    # ========================================

    register_manifest(
        "get_entity_registry_batch",
        make_manifest("get_entity_registry_batch", latency="fast"),
    )

    @mcp.tool()
    async def get_entity_registry_batch(
        entity_ids: str | None = None, fields: str = "all"
    ) -> str:
        """[READ] Fetch entity registry entries with optional filtering by entity IDs and fields.

        Args:
            entity_ids: Comma-separated list of entity IDs, or None for all entities.
            fields: Comma-separated field names or "all". Always includes categories and labels.

        Returns:
            JSON with total_entities and filtered entities list.
        """
        try:
            result = _do_get_entity_registry_batch(
                config_path=config_path,
                entity_ids=entity_ids,
                fields=fields,
            )
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

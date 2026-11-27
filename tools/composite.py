"""
Composite Tools — High-Level AI-Optimised Functions

Combines multiple data sources into single responses to minimise tool calls
and token usage for AI agents.

Problem:  AI agents need 19 tool calls (~15 000 tokens) for a typical
          light-automation investigation.
Solution: Composite functions that aggregate data internally
          → 3-5 calls (~2 500 tokens).

Design Principles
-----------------
* Each composite replaces 3-6 individual tool calls.
* Server-side joins (entity + device + area + automations + state).
* Token-optimised output (blacklisted attributes removed).
* Graceful degradation — partial results on failures, **never crash**.
* ``warnings`` list populated whenever a data source is unavailable so
  the AI agent knows about gaps in the response.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from tools.automations import _extract_entities_recursive
from tools.utils import (
    _build_history_url,
    _error_response,
    _success_response,
    get_best_name,
    load_registry,
    make_ha_request,
    resolve_area_id,
)
from tools.yaml_utils import HomeAssistantLoader

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"

_USEFUL_ATTRS = frozenset(
    {
        "unit_of_measurement",
        "device_class",
        "friendly_name",
        "brightness",
        "color_temp",
        "rgb_color",
        "temperature",
        "humidity",
        "illuminance",
        "battery_level",
        "current_temperature",
        "hvac_action",
        "last_triggered",
        "current",
    }
)


def _minify_state(state_obj: dict[str, Any]) -> dict[str, Any]:
    """Remove blacklisted attributes from state object."""
    attrs = {k: v for k, v in state_obj.get("attributes", {}).items() if k in _USEFUL_ATTRS}
    return {
        "entity_id": state_obj.get("entity_id"),
        "state": state_obj.get("state"),
        "last_changed": state_obj.get("last_changed"),
        "last_updated": state_obj.get("last_updated"),
        "attributes": attrs or None,
    }


def _load_automations(config_path: str) -> tuple[list, str | None]:  # type: ignore[type-arg]
    """Load automations.yaml safely."""
    try:
        fpath = os.path.join(config_path, "automations.yaml")
        if not os.path.exists(fpath):
            return [], "automations.yaml not found — automation data unavailable"
        with open(fpath, encoding="utf-8") as fh:
            data = yaml.load(fh, Loader=HomeAssistantLoader)  # nosec B506 or []
        return data, None
    except Exception as exc:
        return [], f"Failed to load automations.yaml: {exc}"


def _find_automations_for_entity(entity_id: str, automations: list[Any]) -> list[dict]:  # type: ignore[type-arg]
    """Find automations that use a specific entity."""
    results: list[dict] = []  # type: ignore[type-arg]
    for item in automations:
        item_str = str(item)
        if entity_id not in item_str:
            continue
        usage: list[str] = []
        if entity_id in str(item.get("trigger", [])):
            usage.append("trigger")
        if entity_id in str(item.get("condition", [])):
            usage.append("condition")
        if entity_id in str(item.get("action", [])):
            usage.append("action")
        if usage:
            results.append(
                {
                    "id": item.get("id"),
                    "alias": item.get("alias", "Unnamed"),
                    "mode": item.get("mode", "single"),
                    "usage": usage,
                }
            )
    return results


def _get_conflict_analysis(  # type: ignore[no-untyped-def]
    entity_id_or_automations,
    automations: list | None = None,  # type: ignore[type-arg]
) -> dict[str, Any]:
    if automations is None:
        entity_id = None
        automations_list = entity_id_or_automations or []
    else:
        entity_id = entity_id_or_automations
        automations_list = automations or []

    writers: list[dict] = []  # type: ignore[type-arg]
    readers: list[dict] = []  # type: ignore[type-arg]
    for item in automations_list:
        if entity_id and entity_id not in str(item):
            continue
        alias = item.get("alias", "Unnamed")
        mode = item.get("mode", "single")
        if entity_id is None or entity_id in str(item.get("action", [])):
            writers.append({"alias": alias, "mode": mode})
        if entity_id is None or entity_id in str(item.get("trigger", [])):
            readers.append({"alias": alias, "mode": mode})
    return {
        "controlling_automations": writers,
        "triggering_automations": readers,
        "race_condition_risk": len(writers) > 1,
        "feedback_loop_risk": len(writers) > 0 and len(readers) > 0,
    }


# ========================================
# REGISTRY / STATE HELPERS
# ========================================


def _load_registries(config_path: str) -> tuple[list, list, list]:  # type: ignore[type-arg]
    """Load entity / device / area registries (all cached by utils)."""
    ent = load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
    dev = load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
    area = load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
    return ent, dev, area


def _get_all_states(ha_url: str, ha_token: str) -> tuple[dict[str, dict], str | None]:  # type: ignore[type-arg]
    """Return ``(entity_id→state_map, warning_or_none)``."""
    if not ha_url or not ha_token:
        return {}, "HA API credentials not configured — live states unavailable"
    result = make_ha_request(ha_url, ha_token, "/api/states")
    if result["success"]:
        return {s["entity_id"]: s for s in result["data"]}, None
    return {}, f"HA API error: {result.get('error', 'unknown')}"


# ========================================
# TOOL LOGIC (_do_ functions)
# ========================================


def _do_get_entity_with_automations(
    entity_id: str,
    include_automation_code: bool,
    config_path: str,
    ha_url: str,
    ha_token: str,
) -> dict[str, Any]:
    """Full entity context + automations + conflicts."""
    warnings: list[str] = []
    try:
        ent_data, dev_data, area_data = _load_registries(config_path)
        dev_map = {d["id"]: d for d in dev_data}
        area_map = {a["id"]: a for a in area_data}
        automations, auto_warn = _load_automations(config_path)
        if auto_warn:
            warnings.append(auto_warn)

        entity = next((e for e in ent_data if e.get("entity_id") == entity_id), None)
        if not entity:
            return {"success": False, "error": f"Entity '{entity_id}' not found in registry"}

        result: dict[str, Any] = {
            "success": True,
            "entity_id": entity_id,
            "entity_info": {
                "name": get_best_name(entity, "entity"),
                "platform": entity.get("platform"),
                "disabled_by": entity.get("disabled_by"),
                "hidden_by": entity.get("hidden_by"),
            },
            "device_info": None,
            "area_info": None,
            "current_state": None,
            "related_entities": [],
            "automations": [],
            "conflict_analysis": None,
            "issues": [],
            "recommendations": [],
            "warnings": warnings,
        }

        did = entity.get("device_id")
        if did and did in dev_map:
            device = dev_map[did]
            result["device_info"] = {
                "name": get_best_name(device, "device"),
                "manufacturer": device.get("manufacturer"),
                "model": device.get("model"),
                "sw_version": device.get("sw_version"),
            }
            result["related_entities"] = [
                {
                    "entity_id": e.get("entity_id"),
                    "name": get_best_name(e, "entity"),
                }
                for e in ent_data
                if e.get("device_id") == did and e.get("entity_id") != entity_id
            ][:15]

        final_area_id = resolve_area_id(entity, dev_map)
        if final_area_id and final_area_id in area_map:
            result["area_info"] = {
                "id": final_area_id,
                "name": area_map[final_area_id].get("name"),
            }

        states_map, state_warn = _get_all_states(ha_url, ha_token)
        if state_warn:
            warnings.append(state_warn)
        if entity_id in states_map:
            result["current_state"] = _minify_state(states_map[entity_id])
            sv = states_map[entity_id].get("state")
            if sv == "unavailable":
                result["issues"].append("Entity is UNAVAILABLE — check device / integration")
            elif sv == "unknown":
                result["issues"].append("Entity state is UNKNOWN")

        auto_refs = _find_automations_for_entity(entity_id, automations)
        if include_automation_code:
            auto_map = {a.get("alias"): a for a in automations}
            for ref in auto_refs:
                src = auto_map.get(ref["alias"])
                if src:
                    code_item = {k: v for k, v in src.items() if k != "id"}
                    ref["code"] = yaml.dump(
                        code_item,
                        allow_unicode=True,
                        default_flow_style=False,
                        sort_keys=False,
                    )
        result["automations"] = auto_refs

        conflicts = _get_conflict_analysis(entity_id, automations)
        result["conflict_analysis"] = conflicts
        if conflicts["race_condition_risk"]:
            n = len(conflicts["controlling_automations"])
            result["issues"].append(
                f"RACE CONDITION: {n} automations control this entity simultaneously"
            )
            result["recommendations"].append(
                "Use 'mode: restart' or 'mode: single' to prevent conflicts"
            )
        if conflicts["feedback_loop_risk"]:
            result["issues"].append("FEEDBACK LOOP: automation triggers on entity it also controls")
            result["recommendations"].append("Add conditions to prevent infinite loops")

        if entity.get("disabled_by"):
            result["issues"].append(f"Entity DISABLED by: {entity['disabled_by']}")
        if not result["issues"]:
            result["recommendations"].append("Entity appears healthy")

        dq_quality: dict[str, str] = {}
        dq_quality["registry"] = "complete"
        dq_quality["automations"] = "failed" if auto_warn else "complete"
        dq_quality["states_api"] = "failed" if state_warn else "complete"
        if dq_quality["automations"] == "failed" and auto_warn:
            dq_quality["automations_error"] = auto_warn
        if dq_quality["states_api"] == "failed" and state_warn:
            dq_quality["states_error"] = state_warn
        if all(v == "complete" for k, v in dq_quality.items() if not k.endswith("_error")):
            dq_quality = {"overall": "complete"}
        result["data_quality"] = dq_quality

        result["warnings"] = warnings
        return result

    except Exception as exc:
        _logger.exception("_do_get_entity_with_automations failed")
        return {"success": False, "error": str(exc)}


def _do_investigate_entity(
    search_term: str,
    include_automation_code: bool,
    include_history: bool,
    hours_back: int,
    config_path: str,
    ha_url: str,
    ha_token: str,
) -> dict[str, Any]:
    """Comprehensive diagnostics for entity/area in one call."""
    warnings: list[str] = []
    try:
        ent_data, dev_data, area_data = _load_registries(config_path)
        dev_map = {d["id"]: d for d in dev_data}
        area_map = {a["id"]: a for a in area_data}
        automations, auto_warn = _load_automations(config_path)
        if auto_warn:
            warnings.append(auto_warn)

        raw_terms = [t.strip().lower() for t in search_term.split(",") if t.strip()]
        if not raw_terms:
            return {"success": False, "error": "Empty search_term"}

        result: dict[str, Any] = {
            "success": True,
            "search_term": search_term,
            "matched_entities": [],
            "area_context": None,
            "automations": [],
            "conflicts": [],
            "related_sensors": [],
            "issues": [],
            "recommendations": [],
            "warnings": warnings,
            "summary": {},
        }

        matched_area: dict | None = None  # type: ignore[type-arg]
        for term in raw_terms:
            for area in area_data:
                area_name = (area.get("name") or "").lower()
                if term in area_name or area_name in term:
                    matched_area = area
                    break
            if matched_area:
                break

        matched_eids: set[str] = set()
        for entity in ent_data:
            eid = entity.get("entity_id", "")
            name = get_best_name(entity, "entity").lower()
            area_id = resolve_area_id(entity, dev_map)
            hit = any(t in eid.lower() or t in name for t in raw_terms)
            if not hit and matched_area and area_id == matched_area.get("id"):
                hit = True
            if hit:
                matched_eids.add(eid)

        states_map, state_warn = _get_all_states(ha_url, ha_token)
        if state_warn:
            warnings.append(state_warn)

        entities_out: list[dict] = []  # type: ignore[type-arg]
        primary_entity: str | None = None
        _primary_domains = {"light", "switch", "climate", "cover", "fan", "vacuum"}

        for entity in ent_data:
            eid = entity.get("entity_id")
            if eid not in matched_eids:
                continue
            domain = eid.split(".")[0]
            info: dict[str, Any] = {
                "entity_id": eid,
                "name": get_best_name(entity, "entity"),
                "platform": entity.get("platform"),
                "domain": domain,
            }
            did = entity.get("device_id")
            if did and did in dev_map:
                info["device"] = get_best_name(dev_map[did], "device")
                info["manufacturer"] = dev_map[did].get("manufacturer")
            aid = resolve_area_id(entity, dev_map)
            if aid and aid in area_map:
                info["area"] = area_map[aid].get("name")
            if eid in states_map:
                s = states_map[eid]
                info["state"] = s.get("state")
                info["last_changed"] = s.get("last_changed")
                if s.get("state") == "unavailable":
                    result["issues"].append(f"{eid} is UNAVAILABLE")
                elif s.get("state") == "unknown":
                    result["issues"].append(f"{eid} has UNKNOWN state")
            entities_out.append(info)

            if primary_entity is None or domain in _primary_domains:
                if any(
                    t in eid.lower() or t in get_best_name(entity, "entity").lower()
                    for t in raw_terms
                ):
                    primary_entity = eid

        result["matched_entities"] = entities_out[:50]

        if matched_area:
            aid = matched_area["id"]
            ae_count = sum(1 for e in ent_data if resolve_area_id(e, dev_map) == aid)
            ad_count = sum(1 for d in dev_data if d.get("area_id") == aid)
            au = sum(
                1
                for e in ent_data
                if resolve_area_id(e, dev_map) == aid
                and e.get("entity_id") in states_map
                and states_map[e["entity_id"]].get("state") in ("unavailable", "unknown")
            )
            result["area_context"] = {
                "id": aid,
                "name": matched_area.get("name"),
                "devices": ad_count,
                "entities": ae_count,
                "unavailable": au,
            }

        seen_autos: set[str] = set()
        all_auto_refs: list[dict] = []  # type: ignore[type-arg]
        for eid in matched_eids:
            for ref in _find_automations_for_entity(eid, automations):
                alias = ref["alias"]
                if alias not in seen_autos:
                    seen_autos.add(alias)
                    ref["related_entity"] = eid
                    all_auto_refs.append(ref)
        for item in automations:
            alias = item.get("alias", "")
            if any(t in alias.lower() for t in raw_terms) and alias not in seen_autos:
                seen_autos.add(alias)
                all_auto_refs.append(
                    {
                        "alias": alias,
                        "mode": item.get("mode", "single"),
                        "usage": ["name_match"],
                        "related_entity": None,
                    }
                )

        if include_automation_code:
            auto_map = {a.get("alias"): a for a in automations}
            for ref in all_auto_refs:
                src = auto_map.get(ref["alias"])
                if src:
                    code_item = {k: v for k, v in src.items() if k != "id"}
                    ref["code"] = yaml.dump(
                        code_item,
                        allow_unicode=True,
                        default_flow_style=False,
                        sort_keys=False,
                    )

        result["automations"] = all_auto_refs[:20]

        if primary_entity:
            conflicts = _get_conflict_analysis(primary_entity, automations)
            if conflicts["race_condition_risk"] or conflicts["feedback_loop_risk"]:
                result["conflicts"] = {"entity": primary_entity, **conflicts}
                if conflicts["race_condition_risk"]:
                    n = len(conflicts["controlling_automations"])
                    result["issues"].append(
                        f"RACE CONDITION on {primary_entity}: {n} automations control it"
                    )
                if conflicts["feedback_loop_risk"]:
                    result["issues"].append(f"FEEDBACK LOOP on {primary_entity}")

        _sensor_kw = {
            "presence",
            "illuminance",
            "temperature",
            "humidity",
            "motion",
            "occupancy",
            "lux",
            "battery",
        }
        if matched_area:
            aid = matched_area["id"]
            for entity in ent_data:
                eid = entity.get("entity_id", "")
                if resolve_area_id(entity, dev_map) != aid:
                    continue
                if eid.split(".")[0] not in ("sensor", "binary_sensor"):
                    continue
                name_low = get_best_name(entity, "entity").lower()
                if any(kw in eid.lower() or kw in name_low for kw in _sensor_kw):
                    si: dict[str, Any] = {
                        "entity_id": eid,
                        "name": get_best_name(entity, "entity"),
                    }
                    if eid in states_map:
                        si["state"] = states_map[eid].get("state")
                        si["unit"] = (
                            states_map[eid].get("attributes", {}).get("unit_of_measurement")
                        )
                    result["related_sensors"].append(si)
        result["related_sensors"] = result["related_sensors"][:15]

        history_success: bool | None = None
        if include_history and primary_entity and ha_url and ha_token:
            start_time = datetime.now(UTC) - timedelta(hours=min(hours_back, 168))
            url = _build_history_url(start_time, entity_id=primary_entity, minimal=True)
            hist_res = make_ha_request(ha_url, ha_token, url)
            if hist_res["success"] and hist_res["data"] and hist_res["data"][0]:
                history_success = True
                raw = hist_res["data"][0]
                result["history"] = {
                    "entity_id": primary_entity,
                    "period_hours": hours_back,
                    "total_changes": len(raw),
                    "recent_changes": [
                        {"state": c.get("state"), "time": c.get("last_changed")} for c in raw[-10:]
                    ],
                }
            else:
                history_success = False
                warnings.append(f"History fetch failed for {primary_entity}")

        data_quality: dict[str, str] = {}
        data_quality["registry"] = "complete"
        data_quality["automations"] = "failed" if auto_warn else "complete"
        data_quality["states_api"] = "failed" if state_warn else "complete"
        if include_history and primary_entity and ha_url and ha_token:
            data_quality["history"] = "complete" if history_success else "failed"
            if history_success is False:
                data_quality["history_error"] = f"History fetch failed for {primary_entity}"
        if data_quality["automations"] == "failed" and auto_warn:
            data_quality["automations_error"] = auto_warn
        if data_quality["states_api"] == "failed" and state_warn:
            data_quality["states_error"] = state_warn
        if all(v == "complete" for k, v in data_quality.items() if not k.endswith("_error")):
            data_quality = {"overall": "complete"}
        result["data_quality"] = data_quality

        if not result["issues"]:
            result["recommendations"].append("All matched entities appear healthy")
        else:
            if any("UNAVAILABLE" in i for i in result["issues"]):
                result["recommendations"].append(
                    "Check device power/connectivity and integration status"
                )
            if any("RACE CONDITION" in i for i in result["issues"]):
                result["recommendations"].append(
                    "Review automations controlling the same entity — use mode: restart"
                )
            if any("FEEDBACK LOOP" in i for i in result["issues"]):
                result["recommendations"].append("Add state conditions to prevent infinite loops")

        result["summary"] = {
            "entities_found": len(entities_out),
            "automations_found": len(all_auto_refs),
            "issues_count": len(result["issues"]),
            "area": matched_area.get("name") if matched_area else None,
            "primary_entity": primary_entity,
        }
        result["warnings"] = warnings
        return result

    except Exception as exc:
        _logger.exception("_do_investigate_entity failed")
        return {"success": False, "error": str(exc)}


def _do_get_area_diagnostic(
    area_name: str,
    include_automations: bool,
    include_sensors: bool,
    config_path: str,
    ha_url: str,
    ha_token: str,
) -> dict[str, Any]:
    """Full area/room diagnostics in a single query."""
    warnings: list[str] = []
    try:
        ent_data, dev_data, area_data = _load_registries(config_path)
        dev_map = {d["id"]: d for d in dev_data}

        name_lower = area_name.lower()
        area: dict | None = None  # type: ignore[type-arg]
        for a in area_data:
            if a.get("id") == area_name or (a.get("name") or "").lower() == name_lower:
                area = a
                break
        if not area:
            for a in area_data:
                if name_lower in (a.get("name") or "").lower():
                    area = a
                    break
        if not area:
            return {"success": False, "error": f"Area '{area_name}' not found"}

        area_id = area["id"]
        states_map, state_warn = _get_all_states(ha_url, ha_token)
        if state_warn:
            warnings.append(state_warn)

        area_entities = [e for e in ent_data if resolve_area_id(e, dev_map) == area_id]
        by_domain: dict[str, list] = defaultdict(list)  # type: ignore[type-arg]
        unavailable: list[str] = []
        sensor_readings: list[dict] = []  # type: ignore[type-arg]

        for entity in area_entities:
            eid = entity.get("entity_id", "")
            domain = eid.split(".")[0]
            name = get_best_name(entity, "entity")
            info: dict[str, Any] = {"entity_id": eid, "name": name}
            if eid in states_map:
                state = states_map[eid].get("state")
                info["state"] = state
                if state in ("unavailable", "unknown"):
                    unavailable.append(eid)
                if (
                    include_sensors
                    and domain == "sensor"
                    and state not in ("unavailable", "unknown", "")
                ):
                    unit = states_map[eid].get("attributes", {}).get("unit_of_measurement", "")
                    sensor_readings.append(
                        {
                            "entity_id": eid,
                            "name": name,
                            "value": state,
                            "unit": unit,
                        }
                    )
            by_domain[domain].append(info)

        area_automations: list[dict] = []  # type: ignore[type-arg]
        auto_warn: str | None = None
        if include_automations:
            automations, auto_warn = _load_automations(config_path)
            if auto_warn:
                warnings.append(auto_warn)
            area_eids = {e.get("entity_id") for e in area_entities}
            seen: set[str] = set()
            for item in automations:
                item_str = str(item)
                alias = item.get("alias", "Unnamed")
                for eid in area_eids:
                    if eid in item_str and alias not in seen:
                        seen.add(alias)
                        area_automations.append(
                            {
                                "alias": alias,
                                "mode": item.get("mode", "single"),
                                "related_entity": eid,
                            }
                        )
                        break
                if name_lower in alias.lower() and alias not in seen:
                    seen.add(alias)
                    area_automations.append(
                        {
                            "alias": alias,
                            "mode": item.get("mode", "single"),
                            "related_entity": None,
                        }
                    )

        issues: list[str] = []
        if unavailable:
            preview = ", ".join(unavailable[:5])
            suffix = f" (+{len(unavailable) - 5} more)" if len(unavailable) > 5 else ""
            issues.append(f"{len(unavailable)} entities unavailable: {preview}{suffix}")
        if not area_entities:
            issues.append("No entities assigned to this area")

        area_devices = [d for d in dev_data if d.get("area_id") == area_id]

        data_quality: dict[str, str] = {}
        data_quality["registry"] = "complete"
        data_quality["states_api"] = "failed" if state_warn else "complete"
        if include_automations:
            data_quality["automations"] = "failed" if auto_warn else "complete"
            if auto_warn:
                data_quality["automations_error"] = auto_warn
        if data_quality["states_api"] == "failed" and state_warn:
            data_quality["states_error"] = state_warn
        if all(v == "complete" for k, v in data_quality.items() if not k.endswith("_error")):
            data_quality = {"overall": "complete"}

        return {
            "success": True,
            "data_quality": data_quality,
            "area_info": {
                "id": area_id,
                "name": area.get("name"),
                "devices_count": len(area_devices),
                "entities_count": len(area_entities),
                "unavailable_count": len(unavailable),
            },
            "entities_by_domain": {
                domain: {"count": len(ents), "entities": ents[:20]}
                for domain, ents in sorted(by_domain.items())
            },
            "sensor_readings": sensor_readings[:20] if include_sensors else None,
            "automations": area_automations[:15],
            "issues": issues,
            "recommendations": (
                ["Check device connectivity for unavailable entities"]
                if unavailable
                else ["Area looks healthy"]
            ),
            "warnings": warnings,
        }

    except Exception as exc:
        _logger.exception("_do_get_area_diagnostic failed")
        return {"success": False, "error": str(exc)}


def _read_audit_yaml(path: Path, *, mapping: bool) -> tuple[list[dict[str, Any]], str]:
    """Read the directly declared script or scene records for a partial audit."""
    try:
        with path.open(encoding="utf-8") as file:
            data = yaml.load(file, Loader=HomeAssistantLoader)  # nosec B506
    except FileNotFoundError:
        return [], "NOT_PRESENT"
    except (OSError, UnicodeError, yaml.YAMLError):
        return [], "FAILED"
    if data is None:
        return [], "SCANNED"
    if mapping:
        if not isinstance(data, dict):
            return [], "FAILED"
        records = list(data.values())
    else:
        if not isinstance(data, list):
            return [], "FAILED"
        records = data
    if not all(isinstance(record, dict) for record in records):
        return [], "FAILED"
    return records, "SCANNED"


def _read_configuration_root(config_path: str) -> tuple[dict[str, Any] | None, str]:
    """Read the root configuration document with an explicit coverage state."""
    path = Path(config_path) / "configuration.yaml"
    try:
        with path.open(encoding="utf-8") as file:
            data = yaml.load(file, Loader=HomeAssistantLoader)  # nosec B506
    except FileNotFoundError:
        return None, "NOT_PRESENT"
    except (OSError, UnicodeError, yaml.YAMLError):
        return None, "FAILED"
    if data is None:
        return {}, "SCANNED"
    if not isinstance(data, dict):
        return None, "FAILED"
    return data, "SCANNED"


def _read_include_file(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return yaml.load(file, Loader=HomeAssistantLoader)  # nosec B506


_MAX_PACKAGE_INCLUDE_DEPTH = 32


def _resolve_package_include_path(root: Path, base_dir: Path, relative: str) -> Path | None:
    target = (base_dir / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def _resolve_package_data(
    value: Any,
    root: Path,
    base_dir: Path,
    seen: set[Path],
    depth: int,
) -> tuple[Any, bool]:
    """Resolve Home Assistant include directives while preserving coverage gaps."""
    if depth >= _MAX_PACKAGE_INCLUDE_DEPTH:
        return value, False
    if isinstance(value, str):
        match = re.fullmatch(
            r"!include(_dir_(?:list|named|merge_list|merge_named))?\s+(\S+)", value
        )
        if match is None:
            return value, not value.startswith("!include")
        mode_suffix, relative = match.groups()
        mode = f"!include{mode_suffix}" if mode_suffix else "!include"
        target = _resolve_package_include_path(root, base_dir, relative)
        if target is None or target in seen:
            return value, False
        next_seen = seen | {target}
        if mode == "!include":
            try:
                data = _read_include_file(target)
            except (OSError, UnicodeError, yaml.YAMLError):
                return value, False
            return _resolve_package_data(data, root, target.parent, next_seen, depth + 1)
        if not target.is_dir():
            return value, False

        complete = True
        if mode in {"!include_dir_list", "!include_dir_merge_list"}:
            combined: list[Any] = []
            for current_root, dirs, files in os.walk(target, followlinks=False):
                dirs[:] = sorted(name for name in dirs if not name.startswith("."))
                for name in sorted(files):
                    if name.startswith(".") or name == "secrets.yaml" or not name.endswith(".yaml"):
                        continue
                    path = Path(current_root) / name
                    resolved_path = path.resolve()
                    if resolved_path != target and target not in resolved_path.parents:
                        complete = False
                        continue
                    try:
                        data = _read_include_file(resolved_path)
                    except (OSError, UnicodeError, yaml.YAMLError):
                        complete = False
                        continue
                    resolved_data, item_complete = _resolve_package_data(
                        data, root, resolved_path.parent, next_seen | {resolved_path}, depth + 1
                    )
                    complete = complete and item_complete
                    if mode == "!include_dir_list":
                        combined.append(resolved_data)
                    elif isinstance(resolved_data, list):
                        combined.extend(resolved_data)
                    else:
                        complete = False
            return combined, complete

        combined_mapping: dict[Any, Any] = {}
        for current_root, dirs, files in os.walk(target, followlinks=False):
            dirs[:] = sorted(name for name in dirs if not name.startswith("."))
            for name in sorted(files):
                if name.startswith(".") or name == "secrets.yaml" or not name.endswith(".yaml"):
                    continue
                path = Path(current_root) / name
                resolved_path = path.resolve()
                if resolved_path != target and target not in resolved_path.parents:
                    complete = False
                    continue
                try:
                    data = _read_include_file(resolved_path)
                except (OSError, UnicodeError, yaml.YAMLError):
                    complete = False
                    continue
                resolved_data, item_complete = _resolve_package_data(
                    data, root, resolved_path.parent, next_seen, depth + 1
                )
                complete = complete and item_complete
                if mode == "!include_dir_named":
                    key = path.stem
                    if key in combined_mapping:
                        complete = False
                    combined_mapping[key] = resolved_data
                elif isinstance(resolved_data, dict):
                    if combined_mapping.keys() & resolved_data.keys():
                        complete = False
                    combined_mapping.update(resolved_data)
                else:
                    complete = False
        return combined_mapping, complete
    if isinstance(value, dict):
        resolved_mapping: dict[Any, Any] = {}
        complete = True
        for key, item in value.items():
            resolved_mapping[key], item_complete = _resolve_package_data(
                item, root, base_dir, seen, depth
            )
            complete = complete and item_complete
        return resolved_mapping, complete
    if isinstance(value, list):
        resolved_items: list[Any] = []
        complete = True
        for item in value:
            resolved_item, item_complete = _resolve_package_data(item, root, base_dir, seen, depth)
            resolved_items.append(resolved_item)
            complete = complete and item_complete
        return resolved_items, complete
    return value, True


def _package_scan_status(
    records: list[dict[str, Any]], failures: int
) -> tuple[list[dict[str, Any]], str]:
    if failures and not records:
        return [], "FAILED"
    return records, "PARTIAL" if failures else "SCANNED"


def _collect_package_records(
    config_path: str, configuration: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], str]:
    """Collect package records from supported configuration include forms."""
    if not isinstance(configuration, dict) or "homeassistant" not in configuration:
        return [], "NOT_PRESENT"
    root = Path(config_path).resolve()
    configuration_path = root / "configuration.yaml"
    homeassistant = configuration["homeassistant"]
    parent_included = isinstance(homeassistant, str)
    packages_complete = True
    if parent_included:
        homeassistant, packages_complete = _resolve_package_data(
            homeassistant, root, root, {configuration_path}, 0
        )
    if homeassistant is None:
        return [], "NOT_PRESENT"
    if not isinstance(homeassistant, dict):
        return [], "FAILED"
    packages = homeassistant.get("packages")
    if packages is None:
        return [], "NOT_PRESENT"
    if (
        isinstance(packages, str)
        and re.fullmatch(r"!include(_dir_(?:list|named|merge_list|merge_named))?\s+(\S+)", packages)
        is None
    ):
        return [], "PARTIAL"
    if not parent_included:
        packages, packages_complete = _resolve_package_data(
            packages, root, root, {configuration_path}, 0
        )
    if isinstance(packages, list):
        candidates = packages
    elif isinstance(packages, dict):
        candidates = list(packages.values())
    else:
        return [], "FAILED"
    records: list[dict[str, Any]] = []
    failures = 0 if packages_complete else 1
    for candidate in candidates:
        if isinstance(candidate, dict):
            records.append(candidate)
        else:
            failures += 1
    return _package_scan_status(records, failures)


def _collect_template_records(configuration: dict[str, Any] | None) -> tuple[list[Any], str]:
    """Collect directly declared template configuration records."""
    if not isinstance(configuration, dict) or "template" not in configuration:
        return [], "NOT_PRESENT"
    value = configuration["template"]
    if isinstance(value, list):
        return value, "SCANNED"
    if isinstance(value, dict):
        return [value], "SCANNED"
    return [], "FAILED"


def _without_dynamic_strings(data: Any) -> Any:
    """Return source data with Jinja-bearing strings excluded from static scans."""
    if isinstance(data, str):
        return "" if "{{" in data or "{%" in data else data
    if isinstance(data, list):
        return [_without_dynamic_strings(item) for item in data]
    if isinstance(data, dict):
        return {
            key: ""
            if key in {"service", "action"} and isinstance(value, str)
            else _without_dynamic_strings(value)
            for key, value in data.items()
        }
    return data


def _collect_dynamic_references(data: Any, found: set[str]) -> None:
    """Collect references that require runtime Jinja evaluation."""
    if isinstance(data, str):
        if "{{" in data or "{%" in data:
            _extract_entities_recursive({"entity_id": data}, found)
    elif isinstance(data, list):
        for item in data:
            _collect_dynamic_references(item, found)
    elif isinstance(data, dict):
        for value in data.values():
            _collect_dynamic_references(value, found)


def _do_audit_config_orphans(
    config_path: str,
    ha_url: str,
    ha_token: str,
) -> dict[str, Any]:
    """Report incomplete reference-scan candidates without cleanup authority."""
    warnings: list[str] = []
    try:
        ent_reg = load_registry("core.entity_registry", config_path)
        entities = ent_reg.get("data", {}).get("entities", [])
        entity_ids = {e.get("entity_id") for e in entities if e.get("entity_id")}

        automations, auto_warn = _load_automations(config_path)
        if auto_warn:
            warnings.append(auto_warn)

        scripts, scripts_coverage = _read_audit_yaml(
            Path(config_path) / "scripts.yaml", mapping=True
        )
        scenes, scenes_coverage = _read_audit_yaml(Path(config_path) / "scenes.yaml", mapping=False)
        configuration, configuration_coverage = _read_configuration_root(config_path)
        packages, packages_coverage = _collect_package_records(config_path, configuration)
        templates, templates_coverage = _collect_template_records(configuration)

        dashboards = load_registry("lovelace", config_path).get("data", {}).get("dashboards", {})

        referenced: set[str] = set()
        dynamic_references: set[str] = set()
        all_audit_sources = [automations, scripts, scenes, packages, templates]
        all_audit_sources.extend(list(dashboards.values()))
        for source in all_audit_sources:
            for item in source:
                _extract_entities_recursive(_without_dynamic_strings(item), referenced)
                _collect_dynamic_references(item, dynamic_references)

        orphan_entities = sorted(entity_ids - referenced)

        never_triggered: list[dict[str, Any]] = []
        states_api_ok = False
        states_api_error: str | None = None
        if ha_url and ha_token:
            states_data = make_ha_request(ha_url, ha_token, "/api/states")
            if states_data.get("success"):
                states_api_ok = True
                for s in states_data["data"]:
                    eid = s.get("entity_id", "")
                    if eid.startswith("automation."):
                        attrs = s.get("attributes", {})
                        lt = attrs.get("last_triggered")
                        if not lt and s.get("state") != "unavailable":
                            never_triggered.append(
                                {
                                    "entity_id": eid,
                                    "state": s.get("state"),
                                    "alias": attrs.get("friendly_name", ""),
                                }
                            )
            else:
                states_api_error = states_data.get("error", "Unknown error from /api/states")

        all_config_entities: set[str] = set()
        for source in all_audit_sources:
            for item in source:
                _extract_entities_recursive(_without_dynamic_strings(item), all_config_entities)

        broken_references = sorted(all_config_entities - entity_ids)

        unused_blueprints: list[str] = []
        blueprint_coverage = "NOT_PRESENT"
        try:
            blueprints_dir = Path(config_path) / "blueprints"
            if blueprints_dir.is_dir():
                all_blueprints: set[str] = set()
                for domain in ("automation", "script"):
                    domain_dir = blueprints_dir / domain
                    if domain_dir.is_dir():
                        for root, _, files in os.walk(domain_dir):
                            for f in files:
                                if f.endswith(".yaml"):
                                    rel = Path(root).relative_to(blueprints_dir)
                                    all_blueprints.add((rel / f).as_posix())

                used_blueprints: set[str] = set()
                for item in [*automations, *scripts, *packages]:
                    ub = item.get("use_blueprint", {})
                    if isinstance(ub, dict) and "path" in ub:
                        used_blueprints.add(ub["path"])

                for bp in sorted(all_blueprints):
                    if bp not in used_blueprints:
                        unused_blueprints.append(bp)
                if used_blueprints:
                    blueprint_coverage = (
                        "SCANNED"
                        if all((blueprints_dir / bp).is_file() for bp in used_blueprints)
                        else "PARTIAL"
                    )
        except Exception:
            blueprint_coverage = "FAILED"
            warnings.append("Could not scan blueprints directory")

        # This scanner does not cover packages, template ownership, instantiated
        # blueprints or arbitrary dynamic references. Missing observed references
        # are *candidates*, never proven unused entities or a deletion grant.
        reference_coverage = {
            "configuration_yaml": configuration_coverage,
            "automations_yaml": "FAILED" if auto_warn else "SCANNED",
            "scripts_yaml": scripts_coverage,
            "scenes_yaml": scenes_coverage,
            "packages": packages_coverage,
            "templates": templates_coverage,
            "blueprint_instances": blueprint_coverage,
            "dynamic_references": "UNRESOLVED" if dynamic_references else "NONE",
        }
        source_statuses = [
            status for key, status in reference_coverage.items() if key != "dynamic_references"
        ]
        source_scan_complete = all(
            status in {"SCANNED", "NOT_PRESENT"} for status in source_statuses
        )
        warnings.append(
            "Reference scan is incomplete: candidates are not proven orphans "
            "and MUST NOT authorize deletion or destructive cleanup"
        )
        observed_at = datetime.now(UTC).isoformat()
        source_identity = str(Path(config_path).resolve())
        dynamic_unresolved = sorted(dynamic_references)[:100]
        findings = [
            {
                "finding_id": hashlib.sha256(
                    f"{finding_class}\0{entity_id}\0{source_identity}".encode()
                ).hexdigest(),
                "class": finding_class,
                "entity_id": entity_id,
                "observed_at_utc": observed_at,
            }
            for finding_class, entity_ids_for_class in (
                ("REGISTRY_ORPHAN_CANDIDATE", orphan_entities[:100]),
                ("MISSING_REFERENCE_CANDIDATE", broken_references[:100]),
                ("DYNAMIC_REFERENCE_UNRESOLVED", dynamic_unresolved),
            )
            for entity_id in entity_ids_for_class
        ]

        return {
            "reference_evidence_class": "REFERENCE_CANDIDATE",
            "source_snapshot": {
                "config_path": source_identity,
                "observed_at_utc": observed_at,
                "generation": "UNKNOWN",
            },
            "findings": findings,
            "dynamic_unresolved_references": dynamic_unresolved,
            "dynamic_unresolved_count": len(dynamic_references),
            "no_consumers_proven": False,
            "reference_source_coverage": reference_coverage,
            "source_scan_complete": source_scan_complete,
            "reference_coverage_complete": False,
            "safe_to_delete": False,
            "unreferenced_candidates": orphan_entities[:100],
            "unreferenced_candidate_count": len(orphan_entities),
            "orphan_entities": orphan_entities[:100],
            "orphan_count": len(orphan_entities),
            "never_triggered_automations": never_triggered,
            "never_triggered_count": len(never_triggered),
            "never_triggered_status": "unknown" if not states_api_ok else "complete",
            "data_quality": (
                {"overall": "partial", "states_api": "complete", "reference_sources": "partial"}
                if states_api_ok
                else {
                    "overall": "partial",
                    "states_api": "failed",
                    "reference_sources": "partial",
                    "states_error": states_api_error or "Unknown error",
                }
            ),
            "broken_references": broken_references[:100],
            "broken_reference_count": len(broken_references),
            "unused_blueprints": unused_blueprints,
            "unused_blueprint_count": len(unused_blueprints),
            "summary": (
                f"{len(orphan_entities)} unreferenced candidates (NOT proven orphans), "
                f"{len(never_triggered)} automations without recorded trigger, "
                f"{len(broken_references)} potentially broken reference candidates, "
                f"{len(unused_blueprints)} blueprint candidates (partial scan)"
            ),
            "warnings": warnings,
        }

    except Exception as exc:
        _logger.exception("_do_audit_config_orphans failed")
        return {"success": False, "error": str(exc)}


# ================================================================
# TOOL REGISTRATION
# ================================================================


def register_composite_tools(  # type: ignore[no-untyped-def]
    mcp,
    config_path: str,
    ha_url: str,
    ha_token: str,
) -> None:
    """Register composite (aggregated) tools on the given MCP server."""

    @mcp.tool(name="get_entity_with_automations")
    async def get_entity_with_automations(
        entity_id: str,
        include_automation_code: bool = False,
    ) -> str:
        """Composite: full entity context + automations + conflicts in one call.

        Replaces multiple calls: get_entity_context + search_automations_by_entity
        + get_automation_conflicts.

        Args:
            entity_id: Entity id (e.g., "light.yeelink_color2_0510_light").
            include_automation_code: Include full automation YAML (default: False).

        Returns:
            JSON with entity_info, device_info, area_info, current_state,
            related_entities, automations, conflict_analysis, data_quality,
            issues, recommendations, and warnings.
        """
        try:
            data = _do_get_entity_with_automations(
                entity_id, include_automation_code, config_path, ha_url, ha_token
            )
            if data.get("success", False):
                return _success_response(data)
            return _error_response(data.get("error", "unknown error"))
        except Exception as e:
            _logger.exception("get_entity_with_automations failed")
            return _error_response(str(e))

    @mcp.tool(name="investigate_entity")
    async def investigate_entity(
        search_term: str,
        include_automation_code: bool = False,
        include_history: bool = False,
        hours_back: int = 24,
    ) -> str:
        """Super function: comprehensive diagnostics for entity/area in one call.

        Replaces multiple queries (search_entities, get_entity_context,
        get_entity_state, automation searches, area overview, conflict analysis,
        entity state batch). Token-optimized response.

        Args:
            search_term: Room/device/entity name; supports CSV of terms.
            include_automation_code: Include full automation YAML (default: False).
            include_history: Include history of primary entity (default: False).
            hours_back: History window in hours (default: 24, max: 168).

        Returns:
            JSON with matched_entities, area_context, automations, conflicts,
            related_sensors, optional history, data_quality, issues,
            recommendations, summary, and warnings.
        """
        try:
            data = _do_investigate_entity(
                search_term,
                include_automation_code,
                include_history,
                hours_back,
                config_path,
                ha_url,
                ha_token,
            )
            if data.get("success", False):
                return _success_response(data)
            return _error_response(data.get("error", "unknown error"))
        except Exception as e:
            _logger.exception("investigate_entity failed")
            return _error_response(str(e))

    @mcp.tool(name="get_area_diagnostic")
    async def get_area_diagnostic(
        area_name: str,
        include_automations: bool = True,
        include_sensors: bool = True,
    ) -> str:
        """Full area/room diagnostics in a single query: devices, entities, automations, and sensor readings.

        Replaces: get_area_overview() + search_automations(area)
                   + get_entity_state_batch()
        Savings: 3-5 queries → 1 (reduction ~70% tokens)

        Args:
            area_name: Area name or id (e.g. "living_room", "kitchen")
            include_automations: Search automations (default: True)
            include_sensors: Include sensor readings (default: True)

        Returns:
            JSON with area_info, entities_by_domain, sensor_readings,
            automations, data_quality, issues, recommendations, and warnings.
        """
        try:
            data = _do_get_area_diagnostic(
                area_name,
                include_automations,
                include_sensors,
                config_path,
                ha_url,
                ha_token,
            )
            if data.get("success", False):
                return _success_response(data)
            return _error_response(data.get("error", "unknown error"))
        except Exception as e:
            _logger.exception("get_area_diagnostic failed")
            return _error_response(str(e))

    @mcp.tool(name="audit_config_orphans")
    async def audit_config_orphans() -> str:
        """Find orphan entities, never-triggered automations, broken entity references, and unused blueprints.

        Scans entity registry, automations, scripts, scenes, dashboards, and blueprints
        to identify configuration drift and unused resources.

        Args:
            None

        Returns:
            JSON with:
            - orphan_entities: LEGACY alias for advisory reference candidates (NOT proven orphans)
            - never_triggered_automations: automations with no last_triggered
            - broken_references: entity references in configs but missing from registry
            - unused_blueprints: blueprint files not used by any automation or script
            - summary: plain-text summary string
        """
        try:
            data = _do_audit_config_orphans(config_path, ha_url, ha_token)
            if "error" in data:
                return _error_response(data["error"])
            return _success_response(data)
        except Exception as e:
            _logger.exception("audit_config_orphans failed")
            return _error_response(str(e))

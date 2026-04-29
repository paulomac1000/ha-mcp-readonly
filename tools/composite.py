"""
Composite Tools — High-Level AI-Optimised Functions

Combines multiple data sources into single responses to minimise tool calls
and token usage for AI agents.

Problem:  AI agents need 19 tool calls (~15 000 tokens) for a typeeical
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

import json
import os
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import yaml

from tools.utils import (
    get_best_name,
    load_registry,
    make_ha_request,
    resolve_area_id,
)
from tools.yaml_utils import HomeAssistantLoader

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


def _minify_state(state_obj: dict) -> dict:
    """Remove blacklisted attributes from state object."""
    attrs = {k: v for k, v in state_obj.get("attributes", {}).items() if k in _USEFUL_ATTRS}
    return {
        "entity_id": state_obj.get("entity_id"),
        "state": state_obj.get("state"),
        "last_changed": state_obj.get("last_changed"),
        "last_updated": state_obj.get("last_updated"),
        "attributes": attrs or None,
    }


def _load_automations(config_path: str) -> tuple[list, Optional[str]]:
    """Load automations.yaml safely."""
    try:
        fpath = os.path.join(config_path, "automations.yaml")
        if not os.path.exists(fpath):
            return [], "automations.yaml not found — automation data unavailable"
        with open(fpath, "r", encoding="utf-8") as fh:
            data = yaml.load(fh, Loader=HomeAssistantLoader) or []
        return data, None
    except Exception as exc:
        return [], f"Failed to load automations.yaml: {exc}"


def _find_automations_for_entity(entity_id: str, automations: list) -> list[dict]:
    """Find automations that use a specific entity."""
    results: list[dict] = []
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


def _get_conflict_analysis(entity_id_or_automations, automations: list | None = None) -> dict:
    """Analyze potential conflicts for an entity.

    Supports calling with either (_entity_id_, automations) or (automations) only
    for backward compatibility in tests.
    """
    if automations is None:
        entity_id = None
        automations_list = entity_id_or_automations or []
    else:
        entity_id = entity_id_or_automations
        automations_list = automations or []

    writers: list[dict] = []
    readers: list[dict] = []
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


def register_composite_tools(
    mcp,
    config_path: str,
    ha_url: str,
    ha_token: str,
) -> None:
    """Register composite (aggregated) tools on the given MCP server."""

    # ================================================================
    #  INTERNAL HELPERS
    # ================================================================

    def _load_registries() -> tuple[list, list, list]:
        """Load entity / device / area registries (all cached by utils)."""
        ent = load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
        dev = load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
        area = load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
        return ent, dev, area

    def _get_all_states() -> tuple[dict[str, dict], Optional[str]]:
        """Return ``(entity_id→state_map, warning_or_none)``."""
        if not ha_url or not ha_token:
            return {}, "HA API credentials not configured — live states unavailable"
        result = make_ha_request(ha_url, ha_token, "/api/states")
        if result["success"]:
            return {s["entity_id"]: s for s in result["data"]}, None
        return {}, f"HA API error: {result.get('error', 'unknown')}"

    # ================================================================
    #  TOOL 1 — get_entity_with_automations
    # ================================================================

    @mcp.tool(name="get_entity_with_automations")
    async def get_entity_with_automations(
        entity_id: str,
        include_automation_code: bool = False,
    ) -> str:
        """
        Composite: full entity context + automations + conflicts in one call.

        Replaces multiple calls: get_entity_context + search_automations_by_entity
        + get_automation_conflicts. Returns JSON with entity_info, device_info,
        area_info, current_state, related_entities, automations,
        conflict_analysis, issues, recommendations, warnings.

        Args:
            entity_id: Entity id (e.g., "light.yeelink_color2_0510_light").
            include_automation_code: Include full automation YAML (default: False).
        """
        warnings: list[str] = []
        try:
            ent_data, dev_data, area_data = _load_registries()
            dev_map = {d["id"]: d for d in dev_data}
            area_map = {a["id"]: a for a in area_data}
            automations, auto_warn = _load_automations(config_path)
            if auto_warn:
                warnings.append(auto_warn)

            entity = next((e for e in ent_data if e.get("entity_id") == entity_id), None)
            if not entity:
                suggestions = [
                    e.get("entity_id")
                    for e in ent_data
                    if entity_id.split(".")[-1] in (e.get("entity_id") or "")
                ][:5]
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Entity '{entity_id}' not found in registry",
                        "suggestions": suggestions,
                        "warnings": warnings,
                    },
                    indent=2,
                )

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

            # Device
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

            # Area
            final_area_id = resolve_area_id(entity, dev_map)
            if final_area_id and final_area_id in area_map:
                result["area_info"] = {
                    "id": final_area_id,
                    "name": area_map[final_area_id].get("name"),
                }

            # Live state
            states_map, state_warn = _get_all_states()
            if state_warn:
                warnings.append(state_warn)
            if entity_id in states_map:
                result["current_state"] = _minify_state(states_map[entity_id])
                sv = states_map[entity_id].get("state")
                if sv == "unavailable":
                    result["issues"].append("Entity is UNAVAILABLE — check device / integration")
                elif sv == "unknown":
                    result["issues"].append("Entity state is UNKNOWN")

            # Automations
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

            # Conflicts
            conflicts = _get_conflict_analysis(entity_id, automations)
            result["conflict_analysis"] = conflicts
            if conflicts["race_condition_risk"]:
                n = len(conflicts["controlling_automations"])
                result["issues"].append(
                    f"⚠️ RACE CONDITION: {n} automations control this entity simultaneously"
                )
                result["recommendations"].append(
                    "Use 'mode: restart' or 'mode: single' to prevent conflicts"
                )
            if conflicts["feedback_loop_risk"]:
                result["issues"].append(
                    "⚠️ FEEDBACK LOOP: automation triggers on entity it also controls"
                )
                result["recommendations"].append("Add conditions to prevent infinite loops")

            if entity.get("disabled_by"):
                result["issues"].append(f"Entity DISABLED by: {entity['disabled_by']}")
            if not result["issues"]:
                result["recommendations"].append("Entity appears healthy ✅")

            result["warnings"] = warnings
            return json.dumps(result, indent=2, ensure_ascii=False)

        except Exception as exc:
            return json.dumps(
                {
                    "success": False,
                    "error": str(exc),
                    "warnings": warnings,
                },
                indent=2,
            )

    # Legacy alias preserved for backward compatibility (tests expect this name)
    @mcp.tool(name="investigate_entity_mcp_local_lan_mcp")
    async def investigate_entity_legacy(
        search_term: str,
        include_automation_code: bool = False,
        include_history: bool = False,
        hours_back: int = 24,
    ) -> str:
        return await investigate_entity(
            search_term,
            include_automation_code=include_automation_code,
            include_history=include_history,
            hours_back=hours_back,
        )

    @mcp.tool(name="get_entity_with_automations_mcp_local_lan_mcp")
    async def get_entity_with_automations_legacy(
        entity_id: str,
        include_automation_code: bool = False,
    ) -> str:
        """Deprecated alias maintained for backward compatibility."""
        return await get_entity_with_automations(entity_id, include_automation_code)

    # ================================================================
    #  TOOL 2 — investigate_entity  (the "super function")
    # ================================================================

    @mcp.tool(name="investigate_entity")
    async def investigate_entity(
        search_term: str,
        include_automation_code: bool = False,
        include_history: bool = False,
        hours_back: int = 24,
    ) -> str:
        """
        Super function: comprehensive diagnostics for entity/area in one call.

        Replaces multiple queries (search_entities, get_entity_context,
        get_entity_state, automation searches, area overview, conflict analysis,
        entity state batch). Token-optimized response.

        Args:
            search_term: Room/device/entity name; supports CSV of terms.
            include_automation_code: Include full automation YAML (default: False).
            include_history: Include history of primary entity (default: False).
            hours_back: History window in hours (default: 24, max: 168).
        """
        warnings: list[str] = []
        try:
            ent_data, dev_data, area_data = _load_registries()
            dev_map = {d["id"]: d for d in dev_data}
            area_map = {a["id"]: a for a in area_data}
            automations, auto_warn = _load_automations(config_path)
            if auto_warn:
                warnings.append(auto_warn)

            # CSV multi-term support
            raw_terms = [t.strip().lower() for t in search_term.split(",") if t.strip()]
            if not raw_terms:
                return json.dumps({"success": False, "error": "Empty search_term"}, indent=2)

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

            # ── 1. Find matching area ──
            matched_area: Optional[dict] = None
            for term in raw_terms:
                for area in area_data:
                    area_name = (area.get("name") or "").lower()
                    if term in area_name or area_name in term:
                        matched_area = area
                        break
                if matched_area:
                    break

            # ── 2. Find matching entities ──
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

            # ── 3. Live states (single API call) ──
            states_map, state_warn = _get_all_states()
            if state_warn:
                warnings.append(state_warn)

            # ── 4. Build enriched entity list ──
            entities_out: list[dict] = []
            primary_entity: Optional[str] = None
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
                        result["issues"].append(f"❌ {eid} is UNAVAILABLE")
                    elif s.get("state") == "unknown":
                        result["issues"].append(f"⚠️ {eid} has UNKNOWN state")
                entities_out.append(info)

                # Pick primary entity (prefer actionable domains)
                if primary_entity is None or domain in _primary_domains:
                    if any(
                        t in eid.lower() or t in get_best_name(entity, "entity").lower()
                        for t in raw_terms
                    ):
                        primary_entity = eid

            result["matched_entities"] = entities_out[:50]

            # ── 5. Area context ──
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

            # ── 6. Related automations ──
            seen_autos: set[str] = set()
            all_auto_refs: list[dict] = []
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

            # ── 7. Conflict analysis ──
            if primary_entity:
                conflicts = _get_conflict_analysis(primary_entity, automations)
                if conflicts["race_condition_risk"] or conflicts["feedback_loop_risk"]:
                    result["conflicts"] = {"entity": primary_entity, **conflicts}
                    if conflicts["race_condition_risk"]:
                        n = len(conflicts["controlling_automations"])
                        result["issues"].append(
                            f"⚠️ RACE CONDITION on {primary_entity}: {n} automations control it"
                        )
                    if conflicts["feedback_loop_risk"]:
                        result["issues"].append(f"⚠️ FEEDBACK LOOP on {primary_entity}")

            # ── 8. Related sensors ──
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

            # ── 9. History (optional — adds latency) ──
            if include_history and primary_entity and ha_url and ha_token:
                start_time = datetime.now(timezone.utc) - timedelta(hours=min(hours_back, 168))
                url = (
                    f"/api/history/period/{urllib.parse.quote(start_time.isoformat())}"
                    f"?filter_entity_id={urllib.parse.quote(primary_entity)}"
                    f"&minimal_response=true"
                )
                hist_res = make_ha_request(ha_url, ha_token, url)
                if hist_res["success"] and hist_res["data"] and hist_res["data"][0]:
                    raw = hist_res["data"][0]
                    result["history"] = {
                        "entity_id": primary_entity,
                        "period_hours": hours_back,
                        "total_changes": len(raw),
                        "recent_changes": [
                            {"state": c.get("state"), "time": c.get("last_changed")}
                            for c in raw[-10:]
                        ],
                    }
                else:
                    warnings.append(f"History fetch failed for {primary_entity}")

            # ── 10. Recommendations ──
            if not result["issues"]:
                result["recommendations"].append("All matched entities appear healthy ✅")
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
                    result["recommendations"].append(
                        "Add state conditions to prevent infinite loops"
                    )

            result["summary"] = {
                "entities_found": len(entities_out),
                "automations_found": len(all_auto_refs),
                "issues_count": len(result["issues"]),
                "area": matched_area.get("name") if matched_area else None,
                "primary_entity": primary_entity,
            }
            result["warnings"] = warnings
            return json.dumps(result, indent=2, ensure_ascii=False)

        except Exception as exc:
            return json.dumps(
                {
                    "success": False,
                    "error": str(exc),
                    "warnings": warnings,
                },
                indent=2,
            )

    # ================================================================
    #  TOOL 3 — get_area_diagnostic
    # ================================================================

    @mcp.tool(name="get_area_diagnostic_mcp_local_lan_mcp")
    async def get_area_diagnostic(
        area_name: str,
        include_automations: bool = True,
        include_sensors: bool = True,
    ) -> str:
        """
        🚀 COMPOSITE — Full area/room diagnostics in a SINGLE query.

        Replaces: get_area_overview() + search_automations(area)
                   + get_entity_state_batch()
        Savings: 3-5 queries → 1 (reduction ~70% tokens)

        Returns JSON:
            area_info, entities_by_domain, sensor_readings,
            automations, issues, recommendations, warnings

        Args:
            area_name: Area name or id (e.g. "living_room", "kitchen")
            include_automations: Search automations (default: True)
            include_sensors: Include sensor readings (default: True)
        """
        warnings: list[str] = []
        try:
            ent_data, dev_data, area_data = _load_registries()
            dev_map = {d["id"]: d for d in dev_data}

            # Find area (exact → prefix → substring)
            name_lower = area_name.lower()
            area: Optional[dict] = None
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
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Area '{area_name}' not found",
                        "available_areas": [{"id": a["id"], "name": a["name"]} for a in area_data],
                        "warnings": warnings,
                    },
                    indent=2,
                )

            area_id = area["id"]
            states_map, state_warn = _get_all_states()
            if state_warn:
                warnings.append(state_warn)

            area_entities = [e for e in ent_data if resolve_area_id(e, dev_map) == area_id]
            by_domain: dict[str, list] = defaultdict(list)
            unavailable: list[str] = []
            sensor_readings: list[dict] = []

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
                        unit = (
                            states_map[eid]
                            .get("attributes", {})
                            .get(
                                "unit_of_measurement",
                                "",
                            )
                        )
                        sensor_readings.append(
                            {
                                "entity_id": eid,
                                "name": name,
                                "value": state,
                                "unit": unit,
                            }
                        )
                by_domain[domain].append(info)

            area_automations: list[dict] = []
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
                issues.append(f"❌ {len(unavailable)} entities unavailable: {preview}{suffix}")
            if not area_entities:
                issues.append("⚠️ No entities assigned to this area")

            area_devices = [d for d in dev_data if d.get("area_id") == area_id]

            return json.dumps(
                {
                    "success": True,
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
                        else ["Area looks healthy ✅"]
                    ),
                    "warnings": warnings,
                },
                indent=2,
                ensure_ascii=False,
            )

        except Exception as exc:
            return json.dumps(
                {
                    "success": False,
                    "error": str(exc),
                    "warnings": warnings,
                },
                indent=2,
            )

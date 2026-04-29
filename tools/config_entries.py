"""
Config Entry Tools (P0 - Critical)

Provides tools for managing Home Assistant config entries:
- get_config_entry_details(entry_id)
- search_config_entries(domain, title, state, disabled_only)
- diagnose_config_entry(entry_id)
"""

import json
import re
from typing import Any, Dict, List, Optional

from tools.utils import (
    get_registry_devices,
    get_registry_entities,
    load_registry,
    make_ha_request,
    tail_log_file,
)


def register_config_entry_tools(mcp: Any, config_path: str, ha_url: str, ha_token: str) -> None:
    """Register config entry management tools."""

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_config_entries() -> List[Dict[str, Any]]:
        """Load all config entries from registry."""
        data = load_registry("core.config_entries", config_path)
        return data.get("data", {}).get("entries", [])

    def _get_entry_by_id(entry_id: str) -> Optional[Dict[str, Any]]:
        """Find config entry by id."""
        entries = _get_config_entries()
        for entry in entries:
            if entry.get("entry_id") == entry_id:
                return entry
        return None

    def _get_entities_for_entry(entry_id: str) -> List[Dict[str, Any]]:
        """Get all entities belonging to a config entry."""
        entities = get_registry_entities(config_path)
        return [e for e in entities if e.get("config_entry_id") == entry_id]

    def _get_devices_for_entry(entry_id: str) -> List[Dict[str, Any]]:
        """Get all devices belonging to a config entry."""
        devices = get_registry_devices(config_path)
        return [d for d in devices if entry_id in d.get("config_entries", [])]

    def _get_entry_state(
        entry: Dict[str, Any],
        entities: List[Dict[str, Any]],
        ha_url: str,
        ha_token: str,
    ) -> Dict[str, Any]:
        """Determine the effective state of a config entry."""
        if entry.get("disabled_by"):
            return {
                "state": "not_loaded",
                "reason": f"Disabled by: {entry.get('disabled_by')}",
            }

        if not entities:
            return {
                "state": "loaded",
                "reason": "No entities (integration may use different mechanism)",
            }

        # Check entity states via API
        entity_ids = [e.get("entity_id") for e in entities if e.get("entity_id")]
        if not entity_ids:
            return {"state": "loaded", "reason": "No entity IDs found"}

        # Batch check - get all states once
        states_result = make_ha_request(ha_url, ha_token, "/api/states")
        if not states_result.get("success"):
            return {"state": "unknown", "reason": "Could not fetch states"}

        states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

        unavailable_count = 0
        disabled_count = 0
        total = len(entity_ids)

        for eid in entity_ids:
            entity_reg = next((e for e in entities if e.get("entity_id") == eid), {})

            if entity_reg.get("disabled_by"):
                disabled_count += 1
                continue

            state_data = states_map.get(eid, {})
            # Treat missing state as unavailable (no data from HA)
            if not state_data or state_data.get("state") in ["unavailable", "unknown"]:
                unavailable_count += 1

        # Determine state
        active_entities = total - disabled_count

        if active_entities == 0:
            return {"state": "not_loaded", "reason": f"All {total} entities disabled"}

        if unavailable_count == active_entities:
            return {
                "state": "failed",
                "reason": f"All {active_entities} active entities unavailable",
            }

        if unavailable_count > 0:
            return {
                "state": "partial",
                "reason": f"{unavailable_count}/{active_entities} entities unavailable",
            }

        return {"state": "loaded", "reason": "All entities available"}

    # =========================================================================
    # TOOLS
    # =========================================================================

    @mcp.tool()
    async def get_config_entry_details(entry_id: str) -> str:
        """Get details for a single config entry with full context."""
        entry = _get_entry_by_id(entry_id)

        if not entry:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Config entry '{entry_id}' not found",
                    "suggestion": "Use search_config_entries() to find valid entry_id",
                },
                indent=2,
            )

        # Get related entities and devices
        entities = _get_entities_for_entry(entry_id)
        devices = _get_devices_for_entry(entry_id)

        # Determine state
        state_info = _get_entry_state(entry, entities, ha_url, ha_token)

        # Count entity states
        entity_states = {"total": len(entities), "disabled": 0, "enabled": 0}
        for e in entities:
            if e.get("disabled_by"):
                entity_states["disabled"] += 1
            else:
                entity_states["enabled"] += 1

        # Build result
        result = {
            "success": True,
            "entry_id": entry_id,
            "domain": entry.get("domain"),
            "title": entry.get("title"),
            "source": entry.get("source"),
            "version": entry.get("version"),
            "minor_version": entry.get("minor_version"),
            "state": state_info.get("state"),
            "state_reason": state_info.get("reason"),
            "disabled_by": entry.get("disabled_by"),
            "pref_disable_new_entities": entry.get("pref_disable_new_entities"),
            "pref_disable_polling": entry.get("pref_disable_polling"),
            "options_keys": list(entry.get("options", {}).keys())
            if isinstance(entry.get("options"), dict)
            else entry.get("options_keys", []),
            "data_keys": list(entry.get("data", {}).keys())
            if isinstance(entry.get("data"), dict)
            else entry.get("data_keys", []),
            "created_at": entry.get("created_at"),
            "modified_at": entry.get("modified_at"),
            "entities": {
                "total": entity_states["total"],
                "enabled": entity_states["enabled"],
                "disabled": entity_states["disabled"],
                "sample": [
                    {
                        "entity_id": e.get("entity_id"),
                        "platform": e.get("platform"),
                        "disabled_by": e.get("disabled_by"),
                        "device_id": e.get("device_id"),
                    }
                    for e in entities[:10]
                ],
            },
            "devices": [
                {
                    "device_id": d.get("id"),
                    "name": d.get("name_by_user") or d.get("name"),
                    "manufacturer": d.get("manufacturer"),
                    "model": d.get("model"),
                    "area_id": d.get("area_id"),
                    "disabled_by": d.get("disabled_by"),
                    "via_device_id": d.get("via_device_id"),
                }
                for d in devices[:20]
            ],
        }

        if len(entities) > 10:
            result["entities"]["note"] = f"Showing 10 of {len(entities)} entities"

        if len(devices) > 20:
            result["devices_note"] = f"Showing 20 of {len(devices)} devices"

        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    async def search_config_entries(
        domain: Optional[str] = None,
        title: Optional[str] = None,
        state: Optional[str] = None,
        disabled_only: bool = False,
        with_entities: bool = False,
    ) -> str:
        """Search config entries with optional filters."""
        entries = _get_config_entries()
        results = []

        # Pre-load entities if needed
        all_entities = get_registry_entities(config_path) if with_entities or state else []

        for entry in entries:
            # Filter by domain
            if domain and entry.get("domain") != domain.lower():
                continue

            # Filter by title
            if title and title.lower() not in (entry.get("title") or "").lower():
                continue

            # Filter by disabled
            if disabled_only and not entry.get("disabled_by"):
                continue

            # Get entity count if requested
            entity_count = None
            if with_entities or state:
                entry_entities = [
                    e for e in all_entities if e.get("config_entry_id") == entry.get("entry_id")
                ]
                entity_count = len(entry_entities)

            # Filter by state if requested
            if state:
                entry_entities = [
                    e for e in all_entities if e.get("config_entry_id") == entry.get("entry_id")
                ]
                state_info = _get_entry_state(entry, entry_entities, ha_url, ha_token)
                if state_info.get("state") != state:
                    continue

            result_entry = {
                "entry_id": entry.get("entry_id"),
                "domain": entry.get("domain"),
                "title": entry.get("title"),
                "source": entry.get("source"),
                "disabled_by": entry.get("disabled_by"),
                "created_at": entry.get("created_at"),
                "modified_at": entry.get("modified_at"),
            }

            if entity_count is not None:
                result_entry["entities_count"] = entity_count

            results.append(result_entry)

        return json.dumps(
            {
                "success": True,
                "filters": {
                    "domain": domain,
                    "title": title,
                    "state": state,
                    "disabled_only": disabled_only,
                },
                "total_entries": len(entries),
                "matched_count": len(results),
                "entries": results[:50],  # Limit to 50
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def diagnose_config_entry(entry_id: str) -> str:
        """Provide diagnostics for a config entry using registry, API, and logs."""
        entry = _get_entry_by_id(entry_id)

        if not entry:
            return json.dumps(
                {"success": False, "error": f"Config entry '{entry_id}' not found"},
                indent=2,
            )

        domain = entry.get("domain", "")
        title = entry.get("title", "")

        # Get related data
        entities = _get_entities_for_entry(entry_id)
        devices = _get_devices_for_entry(entry_id)

        # Get live states
        states_map = {}
        states_result = make_ha_request(ha_url, ha_token, "/api/states")
        if states_result.get("success"):
            states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

        # Analyze entities
        entities_status = {
            "total": len(entities),
            "enabled": 0,
            "disabled": 0,
            "disabled_by_entry": 0,
            "available": 0,
            "unavailable": 0,
            "unknown": 0,
            "unavailable_entities": [],
            "sample_entities": [],
        }

        for e in entities:
            eid = e.get("entity_id")

            if e.get("disabled_by"):
                entities_status["disabled"] += 1
                if e.get("disabled_by") == "config_entry":
                    entities_status["disabled_by_entry"] += 1
            else:
                entities_status["enabled"] += 1

                # Check live state
                state_data = states_map.get(eid, {})
                state_val = state_data.get("state", "unknown")

                if state_val == "unavailable":
                    entities_status["unavailable"] += 1
                    if len(entities_status["unavailable_entities"]) < 10:
                        entities_status["unavailable_entities"].append(eid)
                elif state_val == "unknown":
                    entities_status["unknown"] += 1
                else:
                    entities_status["available"] += 1

            if len(entities_status["sample_entities"]) < 5:
                entities_status["sample_entities"].append(
                    {
                        "entity_id": eid,
                        "state": states_map.get(eid, {}).get("state"),
                        "disabled_by": e.get("disabled_by"),
                    }
                )

        # Analyze devices
        devices_status = {"total": len(devices), "enabled": 0, "disabled": 0}
        for d in devices:
            if d.get("disabled_by"):
                devices_status["disabled"] += 1
            else:
                devices_status["enabled"] += 1

        # Search logs for errors
        log_errors = []
        try:
            log_lines = tail_log_file(f"{config_path}/home-assistant.log", lines=2000)
            domain_pattern = re.compile(
                rf"\b({re.escape(domain)}|{re.escape(title)})\b", re.IGNORECASE
            )

            for line in log_lines:
                if "ERROR" in line or "WARNING" in line:
                    if domain_pattern.search(line):
                        # Extract timestamp and message
                        match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+ (\w+)", line)
                        if match:
                            log_errors.append(
                                {
                                    "timestamp": match.group(1),
                                    "level": match.group(2),
                                    "message": line[match.end() :].strip()[:200],
                                }
                            )

                        if len(log_errors) >= 10:
                            break
        except Exception as e:
            log_errors.append({"error": f"Could not read logs: {e}"})

        # Determine state
        state_info = _get_entry_state(entry, entities, ha_url, ha_token)

        # Build issues and recommendations
        issues = []
        recommendations = []

        if entry.get("disabled_by"):
            issues.append(
                {
                    "severity": "error",
                    "type": "entry_disabled",
                    "message": f"Config entry is disabled by: {entry.get('disabled_by')}",
                }
            )
            recommendations.append(
                {
                    "priority": "high",
                    "message": "Enable config entry in Settings > Devices & Services",
                }
            )

        if entities_status["unavailable"] > 0:
            pct = (entities_status["unavailable"] / max(entities_status["enabled"], 1)) * 100
            severity = "error" if pct > 50 else "warning"
            issues.append(
                {
                    "severity": severity,
                    "type": "entities_unavailable",
                    "message": f"{entities_status['unavailable']} of {entities_status['enabled']} enabled entities are unavailable ({pct:.0f}%)",
                }
            )
            if pct == 100:
                recommendations.append(
                    {
                        "priority": "high",
                        "message": "Check device connectivity and integration configuration",
                    }
                )
            else:
                recommendations.append(
                    {
                        "priority": "medium",
                        "message": "Some entities unavailable - check specific device connectivity",
                    }
                )

        if entities_status["disabled_by_entry"] > 0:
            issues.append(
                {
                    "severity": "info",
                    "type": "entities_disabled_by_entry",
                    "message": f"{entities_status['disabled_by_entry']} entities disabled due to config entry",
                }
            )

        if log_errors:
            issues.append(
                {
                    "severity": "warning",
                    "type": "log_errors",
                    "message": f"Found {len(log_errors)} recent errors/warnings in logs",
                }
            )
            recommendations.append(
                {
                    "priority": "medium",
                    "message": "Review log_errors for specific error messages",
                }
            )

        if not issues:
            issues.append(
                {
                    "severity": "info",
                    "type": "healthy",
                    "message": "No issues detected ✅",
                }
            )

        result = {
            "success": True,
            "entry_info": {
                "entry_id": entry_id,
                "domain": domain,
                "title": title,
                "source": entry.get("source"),
                "disabled_by": entry.get("disabled_by"),
                "created_at": entry.get("created_at"),
                "modified_at": entry.get("modified_at"),
            },
            "state_analysis": state_info,
            "entities_status": entities_status,
            "devices_status": devices_status,
            "log_errors": log_errors,
            "issues": issues,
            "recommendations": recommendations,
        }

        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    async def list_config_entry_domains() -> str:
        """List all domains (integrations) with counts of config entries."""
        entries = _get_config_entries()

        domain_counts = {}
        domain_disabled = {}

        for entry in entries:
            domain = entry.get("domain", "unknown")
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

            if entry.get("disabled_by"):
                domain_disabled[domain] = domain_disabled.get(domain, 0) + 1

        # Sort by count
        sorted_domains = sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)

        result = {
            "success": True,
            "total_entries": len(entries),
            "total_domains": len(domain_counts),
            "domains": [
                {
                    "domain": domain,
                    "entries": count,
                    "disabled": domain_disabled.get(domain, 0),
                }
                for domain, count in sorted_domains
            ],
        }

        return json.dumps(result, indent=2, ensure_ascii=False)

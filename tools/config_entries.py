"""
Config Entry Tools (P0 - Critical)

Provides tools for managing Home Assistant config entries:
- get_config_entry_details(entry_id)
- search_config_entries(domain, title, state, disabled_only)
- diagnose_config_entry(entry_id)
"""

import logging
import re
from typing import Any

from tools.utils import (
    _error_response,
    _success_response,
    get_registry_devices,
    get_registry_entities,
    load_registry,
    make_ha_request,
    tail_log_file,
)

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


# =============================================================================
# HELPERS
# =============================================================================


def _get_config_entries(config_path: str) -> list[dict[str, Any]]:
    """Load all config entries from registry."""
    data = load_registry("core.config_entries", config_path)
    return data.get("data", {}).get("entries", [])  # type: ignore[no-any-return]


def _get_entry_by_id(entry_id: str, config_path: str) -> dict[str, Any] | None:
    """Find config entry by id."""
    entries = _get_config_entries(config_path)
    for entry in entries:
        if entry.get("entry_id") == entry_id:
            return entry
    return None


def _get_entities_for_entry(entry_id: str, config_path: str) -> list[dict[str, Any]]:
    """Get all entities belonging to a config entry."""
    entities = get_registry_entities(config_path)
    return [e for e in entities if e.get("config_entry_id") == entry_id]


def _get_devices_for_entry(entry_id: str, config_path: str) -> list[dict[str, Any]]:
    """Get all devices belonging to a config entry."""
    devices = get_registry_devices(config_path)
    return [d for d in devices if entry_id in d.get("config_entries", [])]


def _get_entry_state(
    entry: dict[str, Any],
    entities: list[dict[str, Any]],
    ha_url: str,
    ha_token: str,
) -> dict[str, Any]:
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

    entity_ids = [e.get("entity_id") for e in entities if e.get("entity_id")]
    if not entity_ids:
        return {"state": "loaded", "reason": "No entity IDs found"}

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
        if not state_data or state_data.get("state") in ["unavailable", "unknown"]:
            unavailable_count += 1

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


# =============================================================================
# DO FUNCTIONS
# =============================================================================


def _do_get_config_entry_details(
    entry_id: str, ha_url: str, ha_token: str, config_path: str
) -> str:
    """Get details for a single config entry with full context."""
    entry = _get_entry_by_id(entry_id, config_path)

    if not entry:
        return _error_response(f"Config entry '{entry_id}' not found")

    entities = _get_entities_for_entry(entry_id, config_path)
    devices = _get_devices_for_entry(entry_id, config_path)

    state_info = _get_entry_state(entry, entities, ha_url, ha_token)

    entity_states = {"total": len(entities), "disabled": 0, "enabled": 0}
    for e in entities:
        if e.get("disabled_by"):
            entity_states["disabled"] += 1
        else:
            entity_states["enabled"] += 1

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
        result["entities"]["note"] = f"Showing 10 of {len(entities)} entities"  # type: ignore[call-overload, index]

    if len(devices) > 20:
        result["devices_note"] = f"Showing 20 of {len(devices)} devices"

    return _success_response(result)


def _do_search_config_entries(
    domain: str | None,
    title: str | None,
    state: str | None,
    disabled_only: bool,
    with_entities: bool,
    summary_only: bool,
    ha_url: str,
    ha_token: str,
    config_path: str,
) -> str:
    """Search config entries with optional filters."""
    entries = _get_config_entries(config_path)
    results = []

    all_entities = get_registry_entities(config_path) if with_entities or state else []

    for entry in entries:
        if domain and entry.get("domain") != domain.lower():
            continue

        if title and title.lower() not in (entry.get("title") or "").lower():
            continue

        if disabled_only and not entry.get("disabled_by"):
            continue

        entry_entities = [
            e for e in all_entities if e.get("config_entry_id") == entry.get("entry_id")
        ]
        entity_count = len(entry_entities) if (with_entities or state) and entry_entities else None

        resolved_state: str | None = None
        if state:
            state_info = _get_entry_state(entry, entry_entities, ha_url, ha_token)
            resolved_state = state_info.get("state")
            if resolved_state != state:
                continue

        if resolved_state is None:
            if entry.get("disabled_by"):
                resolved_state = "not_loaded"
            elif entry_entities:
                resolved_state = "loaded"
            else:
                resolved_state = "unknown"

        result_entry = {
            "entry_id": entry.get("entry_id"),
            "domain": entry.get("domain"),
            "title": entry.get("title"),
            "source": entry.get("source"),
            "state": resolved_state,
            "disabled_by": entry.get("disabled_by"),
            "created_at": entry.get("created_at"),
            "modified_at": entry.get("modified_at"),
        }

        if entity_count is not None:
            result_entry["entities_count"] = entity_count

        results.append(result_entry)

    if summary_only and len(results) > 50:
        loaded_count = sum(1 for r in results if r.get("state") != "not_loaded")
        not_loaded_count = len(results) - loaded_count
        return _success_response(
            {
                "filters": {
                    "domain": domain,
                    "title": title,
                    "state": state,
                    "disabled_only": disabled_only,
                    "summary_only": True,
                },
                "total_entries": len(entries),
                "matched_count": len(results),
                "loaded": loaded_count,
                "not_loaded": not_loaded_count,
                "sample_entries": results[:20],
            }
        )

    return _success_response(
        {
            "filters": {
                "domain": domain,
                "title": title,
                "state": state,
                "disabled_only": disabled_only,
                "summary_only": summary_only,
            },
            "total_entries": len(entries),
            "matched_count": len(results),
            "entries": results[:50],
        }
    )


def _do_diagnose_config_entry(entry_id: str, ha_url: str, ha_token: str, config_path: str) -> str:
    """Provide diagnostics for a config entry using registry, API, and logs."""
    entry = _get_entry_by_id(entry_id, config_path)

    if not entry:
        return _error_response(f"Config entry '{entry_id}' not found")

    domain = entry.get("domain", "")
    title = entry.get("title", "")

    entities = _get_entities_for_entry(entry_id, config_path)
    devices = _get_devices_for_entry(entry_id, config_path)

    states_map = {}
    states_result = make_ha_request(ha_url, ha_token, "/api/states")
    if states_result.get("success"):
        states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

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
            entities_status["disabled"] += 1  # type: ignore[operator]
            if e.get("disabled_by") == "config_entry":
                entities_status["disabled_by_entry"] += 1  # type: ignore[operator]
        else:
            entities_status["enabled"] += 1  # type: ignore[operator]
            state_data = states_map.get(eid, {})
            state_val = state_data.get("state", "unknown")
            if state_val == "unavailable":
                entities_status["unavailable"] += 1  # type: ignore[operator]
                if len(entities_status["unavailable_entities"]) < 10:  # type: ignore[arg-type]
                    entities_status["unavailable_entities"].append(eid)  # type: ignore[attr-defined]
            elif state_val == "unknown":
                entities_status["unknown"] += 1  # type: ignore[operator]
            else:
                entities_status["available"] += 1  # type: ignore[operator]

        if len(entities_status["sample_entities"]) < 5:  # type: ignore[arg-type]
            entities_status["sample_entities"].append(  # type: ignore[attr-defined]
                {
                    "entity_id": eid,
                    "state": states_map.get(eid, {}).get("state"),
                    "disabled_by": e.get("disabled_by"),
                }
            )

    devices_status = {"total": len(devices), "enabled": 0, "disabled": 0}
    for d in devices:
        if d.get("disabled_by"):
            devices_status["disabled"] += 1
        else:
            devices_status["enabled"] += 1

    log_errors = []
    try:
        log_lines = tail_log_file(f"{config_path}/home-assistant.log", lines=2000)
        domain_pattern = re.compile(rf"\b({re.escape(domain)}|{re.escape(title)})\b", re.IGNORECASE)
        for line in log_lines:
            if "ERROR" in line or "WARNING" in line:
                if domain_pattern.search(line):
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

    state_info = _get_entry_state(entry, entities, ha_url, ha_token)

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

    if entities_status["unavailable"] > 0:  # type: ignore[operator]
        pct = (entities_status["unavailable"] / max(entities_status["enabled"], 1)) * 100  # type: ignore[call-overload]
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

    if entities_status["disabled_by_entry"] > 0:  # type: ignore[operator]
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
                "message": "No issues detected",
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

    return _success_response(result)


def _do_list_config_entry_domains(ha_url: str, ha_token: str, config_path: str) -> str:
    """List all domains (integrations) with counts of config entries."""
    entries = _get_config_entries(config_path)

    domain_counts = {}  # type: ignore[var-annotated]
    domain_disabled = {}  # type: ignore[var-annotated]

    for entry in entries:
        domain = entry.get("domain", "unknown")
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        if entry.get("disabled_by"):
            domain_disabled[domain] = domain_disabled.get(domain, 0) + 1

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

    return _success_response(result)


# =============================================================================
# REGISTRATION
# =============================================================================


def register_config_entry_tools(mcp: Any, config_path: str, ha_url: str, ha_token: str) -> None:
    """Register config entry management tools."""

    @mcp.tool()
    async def get_config_entry_details(entry_id: str) -> str:
        """[READ] Get details for a single config entry with full context.

        Args:
            entry_id: Config entry ID to retrieve details for.

        Returns:
            JSON with entry metadata, entity list, device list, and state analysis.
        """
        try:
            return _do_get_config_entry_details(entry_id, ha_url, ha_token, config_path)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def search_config_entries(
        domain: str | None = None,
        title: str | None = None,
        state: str | None = None,
        disabled_only: bool = False,
        with_entities: bool = False,
        summary_only: bool = False,
    ) -> str:
        """[READ] Search config entries with optional filters.

        Args:
            domain: Filter by integration domain (e.g. "template", "mqtt").
            title: Filter by entry title (case-insensitive substring).
            state: Filter by entry state ("loaded", "not_loaded", "failed", "partial").
            disabled_only: Only return disabled entries.
            with_entities: Include entity count per entry.
            summary_only: Return summary counts instead of full entry list when True.

        Returns:
            JSON with matched entries, filter summary, and result counts.
        """
        try:
            return _do_search_config_entries(
                domain,
                title,
                state,
                disabled_only,
                with_entities,
                summary_only,
                ha_url,
                ha_token,
                config_path,
            )
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def diagnose_config_entry(entry_id: str) -> str:
        """[READ] Provide diagnostics for a config entry using registry, API, and logs.

        Args:
            entry_id: Config entry ID to diagnose.

        Returns:
            JSON with entry info, entity/device status, log errors, issues, and recommendations.
        """
        try:
            return _do_diagnose_config_entry(entry_id, ha_url, ha_token, config_path)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def list_config_entry_domains() -> str:
        """[READ] List all domains (integrations) with counts of config entries."""
        try:
            return _do_list_config_entry_domains(ha_url, ha_token, config_path)
        except Exception as e:
            return _error_response(str(e))

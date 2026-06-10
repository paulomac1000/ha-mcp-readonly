"""
Entity Context Tree Tool

Traces the sources of entity state changes:
- Automation triggers
- User actions (UI, services)
- Device-initiated changes
- Scripts
- Scenes
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from tools.utils import (
    _build_history_url,
    _error_response,
    _success_response,
    get_registry_devices,
    get_registry_entities,
    load_registry,
    make_ha_request,
)

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


def _do_entity_get_context_tree(
    entity_id: str, hours_back: int, ha_url: str, ha_token: str, config_path: str
) -> str:
    """Trace the sources of changes for an entity."""
    if not entity_id or "." not in entity_id:
        return _error_response("Invalid entity_id format. Expected: domain.name")

    state_response = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")
    if not state_response.get("success"):
        return _error_response(f"Entity '{entity_id}' not found in HA")

    current_state = state_response.get("data", {})
    entities = get_registry_entities(config_path)
    entity_reg = next((e for e in entities if e.get("entity_id") == entity_id), None)

    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours_back)

    history_response = make_ha_request(
        ha_url,
        ha_token,
        _build_history_url(start_time, entity_id=entity_id, minimal=True),
    )

    history_entries = []
    if history_response.get("success") and history_response.get("data"):
        entity_history = history_response.get("data", [])
        if entity_history and len(entity_history) > 0:
            for entry in entity_history[0]:
                history_entries.append(
                    {
                        "state": entry.get("state"),
                        "last_changed": entry.get("last_changed"),
                        "attributes": entry.get("attributes", {}),
                    }
                )

    logbook_response = make_ha_request(
        ha_url,
        ha_token,
        f"/api/logbook/{start_time.isoformat()}",
    )

    logbook_entries = []
    entity_logbook = []
    if logbook_response.get("success"):
        logbook_entries = logbook_response.get("data", [])
        for entry in logbook_entries:
            if entry.get("entity_id") == entity_id:
                entity_logbook.append(
                    {
                        "when": entry.get("when"),
                        "name": entry.get("name"),
                        "message": entry.get("message"),
                        "domain": entry.get("domain"),
                        "context_id": entry.get("context", {}).get("id")
                        if entry.get("context")
                        else None,
                    }
                )

    load_registry("automations.yaml", config_path)
    affecting_automations = []

    all_entities = entities
    automation_entities = [
        e for e in all_entities if e.get("entity_id", "").startswith("automation.")
    ]

    for auto_entity in automation_entities:
        affecting_automations.append(
            {
                "automation_id": auto_entity.get("entity_id"),
                "name": auto_entity.get("original_name") or auto_entity.get("name"),
                "note": "May affect this entity - verify in automation configuration",
            }
        )

    sources = defaultdict(lambda: {"count": 0, "events": []})  # type: ignore[var-annotated]

    for entry in entity_logbook[:20]:
        message = (entry.get("message") or "").lower()
        domain = entry.get("domain", "unknown")

        source_type = "unknown"
        if "automation" in message or domain == "automation":
            source_type = "automation"
        elif "script" in message or domain == "script":
            source_type = "script"
        elif "turned on" in message or "turned off" in message:
            source_type = "user_action"
        elif "changed" in message:
            source_type = "device_update"

        sources[source_type]["count"] += 1  # type: ignore[operator]
        sources[source_type]["events"].append(  # type: ignore[attr-defined]
            {
                "time": entry.get("when"),
                "message": entry.get("message"),
                "name": entry.get("name"),
            }
        )

    device_info = None
    if entity_reg and entity_reg.get("device_id"):
        devices = get_registry_devices(config_path)
        device = next((d for d in devices if d.get("id") == entity_reg.get("device_id")), None)
        if device:
            device_info = {
                "device_id": device.get("id"),
                "name": device.get("name_by_user") or device.get("name"),
                "manufacturer": device.get("manufacturer"),
                "model": device.get("model"),
                "area_id": device.get("area_id"),
                "via_device": device.get("via_device_id"),
            }

    context_tree = {
        "entity_id": entity_id,
        "friendly_name": current_state.get("attributes", {}).get("friendly_name"),
        "current_state": current_state.get("state"),
        "analysis_period_hours": hours_back,
        "entity_metadata": {
            "platform": entity_reg.get("platform") if entity_reg else "unknown",
            "device_class": current_state.get("attributes", {}).get("device_class"),
            "unit": current_state.get("attributes", {}).get("unit_of_measurement"),
            "device_info": device_info,
        },
        "recent_changes": {
            "total_history_entries": len(history_entries),
            "total_logbook_entries": len(entity_logbook),
            "last_changed": current_state.get("last_changed"),
            "last_updated": current_state.get("last_updated"),
        },
        "sources_breakdown": dict(sources),
        "affecting_automations": affecting_automations[:10],
        "change_patterns": {
            "note": "Pattern analysis would require more detailed trace data",
            "common_triggers": [
                "time_based"
                if any("time" in str(e.get("message", "")).lower() for e in entity_logbook)
                else None,
                "sensor_based"
                if any("sensor" in str(e.get("message", "")).lower() for e in entity_logbook)
                else None,
                "user_triggered" if sources.get("user_action", {}).get("count", 0) > 0 else None,  # type: ignore[operator]
            ],
        },
        "recommendations": [],
    }

    if sources.get("automation", {}).get("count", 0) > 10:  # type: ignore[operator]
        context_tree["recommendations"].append(
            "High automation activity detected - consider reviewing automation logic"
        )

    if sources.get("unknown", {}).get("count", 0) > len(entity_logbook) * 0.5:  # type: ignore[operator]
        context_tree["recommendations"].append(
            "Many events with unknown source - enable debug logging for better tracing"
        )

    if not any(sources.values()):
        context_tree["recommendations"].append(
            "No recent activity detected - entity may be static or logging disabled"
        )

    return _success_response({"context_tree": context_tree})


def _do_get_context_chain(
    entity_id: str,
    ha_url: str,
    ha_token: str,
    depth: int = 3,
    include_timestamps: bool = True,
) -> str:
    """Trace the context parent_id chain for entity state changes.

    Recursively follows context.parent_id from logbook entries to
    reconstruct the chain of events that led to a state change.

    Args:
        entity_id: Entity to trace context chain for (e.g., "light.living_room")
        depth: Maximum chain depth to follow (capped at 5)
        include_timestamps: Include timestamp per chain step

    Returns:
        JSON with chain (list of steps) and chain_length
    """
    if not entity_id or "." not in entity_id:
        return _error_response("Invalid entity_id format. Expected: domain.name")

    max_depth = min(max(depth, 0), 5)

    start_time = (datetime.now() - timedelta(hours=24)).isoformat()

    entity_response = make_ha_request(
        ha_url, ha_token, f"/api/logbook/{start_time}?entity={entity_id}"
    )
    if not entity_response.get("success"):
        return _error_response(f"Failed to fetch logbook entries for '{entity_id}'")

    entity_entries = entity_response.get("data", [])
    _logger.debug(
        "Fetched %d entity-filtered logbook entries for %s",
        len(entity_entries),
        entity_id,
    )

    full_response = make_ha_request(ha_url, ha_token, f"/api/logbook/{start_time}")
    full_entries: list[dict[str, Any]] = []
    if full_response.get("success"):
        full_entries = full_response.get("data", [])
    _logger.debug("Fetched %d unfiltered logbook entries", len(full_entries))

    context_lookup: dict[str, dict[str, Any]] = {}
    for entry in full_entries:
        ctx_id = entry.get("context_id")
        if ctx_id and ctx_id not in context_lookup:
            context_lookup[ctx_id] = entry

    for entry in entity_entries:
        ctx_id = entry.get("context_id")
        if ctx_id and ctx_id not in context_lookup:
            context_lookup[ctx_id] = entry

    chain: list[dict[str, Any]] = []
    seen: set[str] = set()

    def follow_chain(ctx_id: str, current_depth: int) -> None:
        if current_depth > max_depth or ctx_id in seen:
            return
        if ctx_id not in context_lookup:
            chain.append(
                {
                    "context_id": ctx_id,
                    "parent_id": None,
                    "entity_id": None,
                    "depth": current_depth,
                    "note": "Parent context not found in logbook window",
                }
            )
            seen.add(ctx_id)
            return

        seen.add(ctx_id)
        entry = context_lookup[ctx_id]
        parent_id = entry.get("context_parent_id")

        chain_entry: dict[str, Any] = {
            "context_id": ctx_id,
            "parent_id": parent_id,
            "entity_id": entry.get("entity_id"),
            "depth": current_depth,
        }
        if include_timestamps:
            chain_entry["timestamp"] = entry.get("when")
        chain.append(chain_entry)

        if parent_id:
            follow_chain(parent_id, current_depth + 1)

    for entry in entity_entries:
        ctx_id = entry.get("context_id")
        if ctx_id:
            follow_chain(ctx_id, 0)

    return _success_response({"chain": chain, "chain_length": len(chain)})


def register_entity_context_tools(mcp, config_path: str, ha_url: str, ha_token: str) -> None:  # type: ignore[no-untyped-def]
    """Register entity context tracing tools."""

    @mcp.tool()
    async def get_context_chain(
        entity_id: str,
        depth: int = 3,
        include_timestamps: bool = True,
    ) -> str:
        """[READ] Trace the context parent_id chain for entity state changes.

        Follows context.parent_id links from logbook entries to reconstruct
        the chain of events that led to a state change (e.g. automation →
        script → light).

        Args:
            entity_id: Entity to trace context chain for (e.g., "light.living_room")
            depth: Maximum chain depth to follow (capped at 5, default 3)
            include_timestamps: Include timestamp for each chain step (default True)

        Returns:
            JSON with chain (list of steps, each containing context_id,
            parent_id, entity_id, depth, and optionally timestamp) and
            chain_length.
        """
        try:
            return _do_get_context_chain(entity_id, ha_url, ha_token, depth, include_timestamps)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def entity_get_context_tree(entity_id: str, hours_back: int = 24) -> str:
        """[READ] Trace the sources of changes for an entity.

        Analyzes automation traces, logbook entries, and history
        to determine what triggered recent state changes.

        Args:
            entity_id: The entity to analyze (e.g., "light.living_room")
            hours_back: How many hours of history to analyze (default 24)

        Returns:
            JSON with context tree showing:
            - current_state: current entity state
            - recent_changes: list of recent state changes with sources
            - sources: breakdown by source type (automation/user/device/script)
            - automation_triggers: which automations affect this entity
            - potential_sources: inferred sources from configuration
        """
        try:
            return _do_entity_get_context_tree(entity_id, hours_back, ha_url, ha_token, config_path)
        except Exception as e:
            return _error_response(str(e))

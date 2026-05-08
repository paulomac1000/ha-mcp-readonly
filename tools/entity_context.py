"""
Entity Context Tree Tool

Traces the sources of entity state changes:
- Automation triggers
- User actions (UI, services)
- Device-initiated changes
- Scripts
- Scenes
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta

from tools.utils import get_registry_entities, load_registry, make_ha_request


def register_entity_context_tools(mcp, config_path: str, ha_url: str, ha_token: str):
    """Register entity context tracing tools."""

    @mcp.tool()
    async def entity_get_context_tree(entity_id: str, hours_back: int = 24) -> str:
        """
        Trace the sources of changes for an entity.

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
        if not entity_id or "." not in entity_id:
            return json.dumps(
                {
                    "success": False,
                    "error": "Invalid entity_id format. Expected: domain.name",
                },
                indent=2,
            )

        # Get current entity state
        state_response = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")
        if not state_response.get("success"):
            return json.dumps(
                {
                    "success": False,
                    "error": f"Entity '{entity_id}' not found in HA",
                    "details": state_response.get("error"),
                },
                indent=2,
            )

        current_state = state_response.get("data", {})

        # Get entity registry info
        entities = get_registry_entities(config_path)
        entity_reg = next((e for e in entities if e.get("entity_id") == entity_id), None)

        # Try to get history
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours_back)

        history_response = make_ha_request(
            ha_url,
            ha_token,
            f"/api/history/period/{start_time.isoformat()}?filter_entity_id={entity_id}&minimal_response=True",
        )

        history_entries = []
        if history_response.get("success") and history_response.get("data"):
            # History returns a list of lists (one per entity)
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

        # Try to get logbook entries
        logbook_response = make_ha_request(
            ha_url,
            ha_token,
            f"/api/logbook/{start_time.isoformat()}",
        )

        logbook_entries = []
        entity_logbook = []
        if logbook_response.get("success"):
            logbook_entries = logbook_response.get("data", [])
            # Filter for our entity
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

        # Analyze automations that affect this entity
        load_registry("automations.yaml", config_path)
        affecting_automations = []

        # Also check for automation entities that reference this entity
        all_entities = entities
        automation_entities = [
            e for e in all_entities if e.get("entity_id", "").startswith("automation.")
        ]

        for auto_entity in automation_entities:
            # We'd need to parse the automation config to find references
            # For now, we'll note the automation exists
            affecting_automations.append(
                {
                    "automation_id": auto_entity.get("entity_id"),
                    "name": auto_entity.get("original_name") or auto_entity.get("name"),
                    "note": "May affect this entity - verify in automation configuration",
                }
            )

        # Build source breakdown
        sources = defaultdict(lambda: {"count": 0, "events": []})

        for entry in entity_logbook[:20]:  # Last 20 events
            message = (entry.get("message") or "").lower()
            domain = entry.get("domain", "unknown")

            # Categorize source
            source_type = "unknown"
            if "automation" in message or domain == "automation":
                source_type = "automation"
            elif "script" in message or domain == "script":
                source_type = "script"
            elif "turned on" in message or "turned off" in message:
                # Likely user action via UI
                source_type = "user_action"
            elif "changed" in message:
                source_type = "device_update"

            sources[source_type]["count"] += 1
            sources[source_type]["events"].append(
                {
                    "time": entry.get("when"),
                    "message": entry.get("message"),
                    "name": entry.get("name"),
                }
            )

        # Get related device info
        device_info = None
        if entity_reg and entity_reg.get("device_id"):
            from tools.utils import get_registry_devices

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

        # Build context tree
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
            "affecting_automations": affecting_automations[:10],  # Top 10
            "change_patterns": {
                "note": "Pattern analysis would require more detailed trace data",
                "common_triggers": [
                    "time_based"
                    if any("time" in str(e.get("message", "")).lower() for e in entity_logbook)
                    else None,
                    "sensor_based"
                    if any("sensor" in str(e.get("message", "")).lower() for e in entity_logbook)
                    else None,
                    "user_triggered"
                    if sources.get("user_action", {}).get("count", 0) > 0
                    else None,
                ],
            },
            "recommendations": [],
        }

        # Add recommendations based on analysis
        if sources.get("automation", {}).get("count", 0) > 10:
            context_tree["recommendations"].append(
                "High automation activity detected - consider reviewing automation logic"
            )

        if sources.get("unknown", {}).get("count", 0) > len(entity_logbook) * 0.5:
            context_tree["recommendations"].append(
                "Many events with unknown source - enable debug logging for better tracing"
            )

        if not any(sources.values()):
            context_tree["recommendations"].append(
                "No recent activity detected - entity may be static or logging disabled"
            )

        return json.dumps(
            {"success": True, "context_tree": context_tree},
            indent=2,
            ensure_ascii=False,
        )

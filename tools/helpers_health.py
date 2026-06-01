"""
Helper Health Diagnostics
Detects stuck input_booleans, timers, and counters that have not changed state
for an extended period — a common root cause of silent system failures.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from tools.manifests import make_manifest, register_manifest
from tools.utils import _error_response, _success_response, create_error_response, make_ha_request

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


def _do_diagnose_stuck_helpers(
    stale_hours: int,
    entity_ids: str | None,
    ha_url: str,
    ha_token: str,
) -> dict[str, Any]:
    """Scan input_boolean, timer, counter entities for stuck state."""
    result = make_ha_request(ha_url, ha_token, "/api/states", timeout=15)
    if not result.get("success"):
        return create_error_response(
            "DEPENDENCY_MISSING",
            "Failed to fetch states from HA API",
            True,
            "Check HA connectivity and retry",
        )

    entities = result.get("data", [])
    if isinstance(entities, dict):
        entities = entities.get("entities", entities.get("result", []))

    target_domains = {"input_boolean", "timer", "counter"}
    if entity_ids:
        requested = set(entity_ids.replace(",", " ").split())
    else:
        requested = None

    now = datetime.now(UTC)
    stuck = []

    for ent in entities:
        entity_id = ent.get("entity_id", "")
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        if domain not in target_domains:
            continue
        if requested and entity_id not in requested:
            continue

        last_changed_str = ent.get("last_changed", "")
        if not last_changed_str:
            continue
        try:
            last_changed = datetime.fromisoformat(last_changed_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        hours_stuck = (now - last_changed).total_seconds() / 3600
        if hours_stuck < stale_hours:
            continue

        state = ent.get("state", "unknown")
        if hours_stuck >= 168:
            severity = "critical"
        elif hours_stuck >= 72:
            severity = "error"
        elif hours_stuck >= stale_hours:
            severity = "warning"
        else:
            severity = "info"

        stuck.append(
            {
                "entity_id": entity_id,
                "state": state,
                "last_changed": last_changed_str,
                "hours_stuck": round(hours_stuck, 1),
                "severity": severity,
                "domain": domain,
                "friendly_name": ent.get("attributes", {}).get("friendly_name", ""),
            }
        )

    stuck.sort(key=lambda x: x["hours_stuck"], reverse=True)

    recommendations = []
    if stuck:
        recommendations.append(
            "Review stuck helpers and investigate why they are not being updated. "
            "Common causes: restart-interrupted delay patterns, missing triggers, "
            "broken automations."
        )

    return {
        "success": True,
        "total_helpers_scanned": sum(
            1 for e in entities if e.get("entity_id", "").split(".", 1)[0] in target_domains
        ),
        "stuck_count": len(stuck),
        "stale_threshold_hours": stale_hours,
        "stuck_helpers": stuck,
        "recommendations": recommendations,
    }


def register_helpers_health_tools(mcp, ha_url: str, ha_token: str) -> None:  # type: ignore[no-untyped-def]
    """Register helper health diagnostic tools.

    Args:
        mcp: FastMCP instance.
        ha_url: Home Assistant API URL.
        ha_token: Authorization token.
    """
    register_manifest(
        "diagnose_stuck_helpers",
        make_manifest("diagnose_stuck_helpers", latency="moderate", cost="moderate"),
    )

    @mcp.tool()
    def diagnose_stuck_helpers(
        stale_hours: int = 24,
        entity_ids: str | None = None,
    ) -> str:
        """[READ] Detects input_boolean, timer, and counter entities stuck in one state.

        Scans helper entities and flags those whose ``last_changed`` timestamp is
        older than ``stale_hours``. Useful for catching restart-interrupted
        delay patterns and broken automations.

        Args:
            stale_hours: Minimum hours without state change to flag (default: 24).
            entity_ids: Optional comma-separated entity IDs to check. If omitted, all helpers are scanned.

        Returns:
            JSON with stuck_helpers list, severity levels, and recommendations.
        """
        try:
            data = _do_diagnose_stuck_helpers(stale_hours, entity_ids, ha_url, ha_token)
            if data.get("success") is False:
                return _error_response(data.get("error", data))
            return _success_response(data)
        except Exception as exc:
            _logger.exception("diagnose_stuck_helpers failed")
            return _error_response(str(exc))

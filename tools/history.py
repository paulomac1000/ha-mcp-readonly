"""
History Analysis Tools (P1 - Important).

Provides tools for analyzing entity history:
- get_entity_state_history_summary(entity_id, hours_back)
- get_recent_state_changes(minutes, domains)
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from tools.utils import make_ha_request

# =============================================================================
# CONstateTS
# =============================================================================

MAX_HISTORY_HOURS: int = 168
MAX_RECENT_MINUTES: int = 60
MAX_RETURNED_CHANGES: int = 50
RAPID_CYCLING_SECONDS: int = 60
RAPID_CYCLING_COUNT: int = 3


def register_history_tools(mcp, ha_url: str, ha_token: str) -> None:
    """Register history analysis tools on the MCP server."""

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _parse_timestamp(ts_str: str) -> Optional[datetime]:
        """Parse ISO timestamp to a ``datetime`` object."""
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _calculate_duration(start: datetime, end: datetime) -> float:
        """Calculate duration in minutes."""
        return (end - start).total_seconds() / 60

    # =========================================================================
    # TOOLS
    # =========================================================================

    @mcp.tool()
    async def get_entity_state_history_summary(entity_id: str, hours_back: int = 24) -> str:
        """
        Summarize entity state history instead of returning raw rows.

        Args:
            entity_id: Entity identifier (e.g., ``switch.test``).
            hours_back: Number of hours to analyze (capped to 168).

        Returns:
            JSON string with summary fields:
            - state_changes: number of changes
            - states_breakdown: {state: {count, total_duration, avg_duration}}
            - anomalies: detected anomalies (e.g., rapid cycling)
            - last_5_changes: tail of the processed changes
        """
        hours_back = min(max(int(hours_back), 1), MAX_HISTORY_HOURS)

        # Calculate start time
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=hours_back)

        # Fetch history
        url = f"/api/history/period/{start_time.isoformat()}?filter_entity_id={entity_id}&minimal_response=false"
        result = make_ha_request(ha_url, ha_token, url)

        if not result["success"]:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Failed to fetch history: {result.get('error')}",
                },
                indent=2,
            )

        history_data = result["data"]
        if not history_data or not history_data[0]:
            return json.dumps(
                {
                    "success": True,
                    "entity_id": entity_id,
                    "period_hours": hours_back,
                    "state_changes": 0,
                    "message": "No history found for this period",
                },
                indent=2,
            )

        changes = history_data[0]

        # Analyze changes
        states_stats = defaultdict(lambda: {"count": 0, "total_duration_min": 0.0})
        anomalies = []
        processed_changes = []

        last_change_time = None
        rapid_changes = 0

        for i, state in enumerate(changes):
            state_val = state.get("state")
            if state_val in ["unavailable", "unknown"]:
                continue

            current_time = _parse_timestamp(state.get("last_changed"))
            if not current_time:
                continue

            # Calculate duration until next change or now
            if i < len(changes) - 1:
                next_time = _parse_timestamp(changes[i + 1].get("last_changed"))
                duration = _calculate_duration(current_time, next_time) if next_time else 0
            else:
                duration = _calculate_duration(current_time, end_time)

            # Update stats
            states_stats[state_val]["count"] += 1
            states_stats[state_val]["total_duration_min"] += duration

            # Detect rapid cycling (change within 1 minute)
            if (
                last_change_time
                and (current_time - last_change_time).total_seconds() < RAPID_CYCLING_SECONDS
            ):
                rapid_changes += 1
            else:
                rapid_changes = 0

            if rapid_changes >= RAPID_CYCLING_COUNT and "Rapid cycling" not in str(anomalies):
                anomalies.append(f"Rapid cycling detected around {current_time.strftime('%H:%M')}")

            last_change_time = current_time

            # Simplify for output
            processed_changes.append(
                {
                    "state": state_val,
                    "changed": state.get("last_changed"),
                    "duration_min": round(duration, 1),
                }
            )

        # Calculate averages
        final_stats = {}
        for state, stats in states_stats.items():
            avg = stats["total_duration_min"] / max(stats["count"], 1)
            final_stats[state] = {
                "count": stats["count"],
                "total_duration_min": round(stats["total_duration_min"], 1),
                "avg_duration_min": round(avg, 1),
                "percentage": round((stats["total_duration_min"] / (hours_back * 60)) * 100, 1),
            }

        # Prepare result
        response = {
            "success": True,
            "entity_id": entity_id,
            "period_hours": hours_back,
            "total_changes": len(processed_changes),
            "states_breakdown": final_stats,
            "anomalies": anomalies,
            "current_state_duration_min": processed_changes[-1]["duration_min"]
            if processed_changes
            else 0,
            "last_5_changes": processed_changes[-5:],
        }

        return json.dumps(response, indent=2, ensure_ascii=False)

    @mcp.tool()
    async def get_recent_state_changes(minutes: int = 10, domains: Optional[str] = None) -> str:
        """
        Retrieve all state changes from the last ``minutes`` minutes.

        Args:
            minutes: Window length in minutes (capped to 60).
            domains: Optional domain filter (comma-separated, e.g., "switch,climate").

        Returns:
            JSON string with:
            - total_changes
            - changes[]: {timestamp, entity_id, from, to}
            - most_active_entities[]
        """
        minutes = min(max(int(minutes), 1), MAX_RECENT_MINUTES)

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=minutes)

        # Get all history
        url = f"/api/history/period/{start_time.isoformat()}?minimal_response=false"
        result = make_ha_request(ha_url, ha_token, url)

        if not result["success"]:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Failed to fetch history: {result.get('error')}",
                },
                indent=2,
            )

        history_data = result["data"]
        changes_list = []
        entity_counts = Counter()

        domain_list = [d.strip().lower() for d in domains.split(",")] if domains else None

        # history_data is [[state1, state2], [state1, state2]] (list of lists per entity)
        for entity_history in history_data:
            if not entity_history:
                continue

            entity_id = entity_history[0].get("entity_id")

            # Filter by domain
            if domain_list:
                domain = entity_id.split(".")[0]
                if domain not in domain_list:
                    continue

            # Process changes
            prev_state = None
            for state in entity_history:
                ts = _parse_timestamp(state.get("last_changed"))
                if not ts or ts < start_time:
                    prev_state = state.get("state")
                    continue

                # It's a change within window
                changes_list.append(
                    {
                        "timestamp": state.get("last_changed"),
                        "entity_id": entity_id,
                        "to_state": state.get("state"),
                        "from_state": prev_state,  # Note: might be None if first record
                        "user_id": state.get("context", {}).get("user_id"),
                    }
                )

                entity_counts[entity_id] += 1
                prev_state = state.get("state")

        # Sort by timestamp (newest first)
        changes_list.sort(key=lambda x: x["timestamp"], reverse=True)

        response = {
            "success": True,
            "period_minutes": minutes,
            "total_changes": len(changes_list),
            "most_active_entities": [
                {"entity_id": e, "changes": c} for e, c in entity_counts.most_common(5)
            ],
            "changes": changes_list[:MAX_RETURNED_CHANGES],
        }

        if len(changes_list) > MAX_RETURNED_CHANGES:
            response["note"] = f"Showing {MAX_RETURNED_CHANGES} of {len(changes_list)} changes"

        return json.dumps(response, indent=2, ensure_ascii=False)

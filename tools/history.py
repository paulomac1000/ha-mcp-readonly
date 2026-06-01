"""
History Analysis Tools (P1 - Important).

Provides tools for analyzing entity history:
- get_entity_state_history_summary(entity_id, hours_back)
- get_recent_state_changes(minutes, domains)
"""

import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from tools.utils import _error_response, _success_response, make_ha_request

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"

# =============================================================================
# CONFIG
# =============================================================================

MAX_HISTORY_HOURS: int = 168
MAX_RECENT_MINUTES: int = 60
MAX_RETURNED_CHANGES: int = 50
RAPID_CYCLING_SECONDS: int = 60
RAPID_CYCLING_COUNT: int = 3


# =============================================================================
# HELPERS
# =============================================================================


def _parse_timestamp(ts_str: str) -> datetime | None:
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


# =============================================================================
# EXTRACTED LOGIC
# =============================================================================


def _do_get_entity_state_history_summary(
    entity_id: str, hours_back: int, ha_url: str, ha_token: str,
    group_by: str | None = None,
) -> str:
    """Summarize entity state history instead of returning raw rows.

    Args:
        entity_id: Entity identifier.
        hours_back: Number of hours to analyze (capped to 168).
        ha_url: Home Assistant API URL.
        ha_token: Authorization token.
        group_by: Optional aggregation — ``"hour"`` or ``"day"`` to return
            grouped statistics instead of raw change entries.
    """
    hours_back = min(max(int(hours_back), 1), MAX_HISTORY_HOURS)

    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(hours=hours_back)

    url = f"/api/history/period/{start_time.isoformat()}?filter_entity_id={entity_id}&minimal_response=false"
    result = make_ha_request(ha_url, ha_token, url)

    if not result["success"]:
        return _error_response(f"Failed to fetch history: {result.get('error')}")

    history_data = result["data"]
    if not history_data or not history_data[0]:
        return _success_response(
            {
                "entity_id": entity_id,
                "period_hours": hours_back,
                "state_changes": 0,
                "message": "No history found for this period",
            }
        )

    changes = history_data[0]

    states_stats = defaultdict(lambda: {"count": 0, "total_duration_min": 0.0})  # type: ignore[var-annotated]
    anomalies = []  # type: ignore[var-annotated]
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

        if i < len(changes) - 1:
            next_time = _parse_timestamp(changes[i + 1].get("last_changed"))
            duration = _calculate_duration(current_time, next_time) if next_time else 0
        else:
            duration = _calculate_duration(current_time, end_time)

        states_stats[state_val]["count"] += 1
        states_stats[state_val]["total_duration_min"] += duration

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

        processed_changes.append(
            {
                "state": state_val,
                "changed": state.get("last_changed"),
                "duration_min": round(duration, 1),
            }
        )

    final_stats = {}
    for state, stats in states_stats.items():
        avg = stats["total_duration_min"] / max(stats["count"], 1)
        final_stats[state] = {
            "count": stats["count"],
            "total_duration_min": round(stats["total_duration_min"], 1),
            "avg_duration_min": round(avg, 1),
            "percentage": round((stats["total_duration_min"] / (hours_back * 60)) * 100, 1),
        }

    grouped = None
    if group_by in ("hour", "day") and processed_changes:
        buckets: dict[str, list[float]] = {}
        for ch in processed_changes:
            ts = _parse_timestamp(ch["changed"])
            if not ts:
                continue
            if group_by == "hour":
                bucket_key = ts.strftime("%Y-%m-%dT%H:00")
            else:
                bucket_key = ts.strftime("%Y-%m-%d")
            state_val = ch["state"]
            try:
                numeric_val = float(state_val)
            except (ValueError, TypeError):
                numeric_val = 0.0
            buckets.setdefault(bucket_key, []).append(numeric_val)

        grouped = {}
        for bucket, values in sorted(buckets.items()):
            grouped[bucket] = {
                "count": len(values),
                "min": round(min(values), 3),
                "max": round(max(values), 3),
                "avg": round(sum(values) / len(values), 3),
            }

    response_data: dict[str, Any] = {
        "entity_id": entity_id,
        "period_hours": hours_back,
        "total_changes": len(processed_changes),
        "states_breakdown": final_stats,
        "anomalies": anomalies,
        "current_state_duration_min": processed_changes[-1]["duration_min"]
        if processed_changes
        else 0,
    }
    if grouped is not None:
        response_data["grouped_by"] = group_by
        response_data["grouped"] = grouped
    else:
        response_data["last_5_changes"] = processed_changes[-5:]

    return _success_response(response_data)


def _do_get_recent_state_changes(
    minutes: int, domains: str | None, ha_url: str, ha_token: str
) -> str:
    """Retrieve all state changes from the last ``minutes`` minutes."""
    minutes = min(max(int(minutes), 1), MAX_RECENT_MINUTES)

    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(minutes=minutes)

    url = f"/api/history/period/{start_time.isoformat()}?minimal_response=false"
    result = make_ha_request(ha_url, ha_token, url)

    if not result["success"]:
        return _error_response(f"Failed to fetch history: {result.get('error')}")

    history_data = result["data"]
    changes_list = []
    entity_counts = Counter()  # type: ignore[var-annotated]

    domain_list = [d.strip().lower() for d in domains.split(",")] if domains else None

    for entity_history in history_data:
        if not entity_history:
            continue

        entity_id = entity_history[0].get("entity_id")

        if domain_list:
            domain = entity_id.split(".")[0]
            if domain not in domain_list:
                continue

        prev_state = None
        for state in entity_history:
            ts = _parse_timestamp(state.get("last_changed"))
            if not ts or ts < start_time:
                prev_state = state.get("state")
                continue

            changes_list.append(
                {
                    "timestamp": state.get("last_changed"),
                    "entity_id": entity_id,
                    "to_state": state.get("state"),
                    "from_state": prev_state,
                    "user_id": state.get("context", {}).get("user_id"),
                }
            )

            entity_counts[entity_id] += 1
            prev_state = state.get("state")

    changes_list.sort(key=lambda x: x["timestamp"], reverse=True)

    response = {
        "period_minutes": minutes,
        "total_changes": len(changes_list),
        "most_active_entities": [
            {"entity_id": e, "changes": c} for e, c in entity_counts.most_common(5)
        ],
        "changes": changes_list[:MAX_RETURNED_CHANGES],
    }

    if len(changes_list) > MAX_RETURNED_CHANGES:
        response["note"] = f"Showing {MAX_RETURNED_CHANGES} of {len(changes_list)} changes"

    return _success_response(response)


def register_history_tools(mcp, ha_url: str, ha_token: str) -> None:  # type: ignore[no-untyped-def]
    """Register history analysis tools on the MCP server."""

    @mcp.tool()
    async def get_entity_state_history_summary(
        entity_id: str,
        hours_back: int = 24,
        group_by: str | None = None,
    ) -> str:
        """[READ] Summarize entity state history instead of returning raw rows.

        Args:
            entity_id: Entity identifier (e.g., ``switch.test``).
            hours_back: Number of hours to analyze (capped to 168).
            group_by: Optional aggregation level — ``"hour"`` or ``"day"``.
                When set, returns grouped statistics (count, min, max, avg)
                instead of raw change entries (default: None).

        Returns:
            JSON string with summary fields:
            - state_changes: number of changes
            - states_breakdown: {state: {count, total_duration, avg_duration}}
            - anomalies: detected anomalies (e.g., rapid cycling)
            - grouped: when group_by is set, time-bucketed statistics
            - last_5_changes: tail of the processed changes (when group_by is None)
        """
        try:
            return _do_get_entity_state_history_summary(
                entity_id, hours_back, ha_url, ha_token, group_by
            )
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_recent_state_changes(minutes: int = 10, domains: str | None = None) -> str:
        """[READ] Retrieve all state changes from the last ``minutes`` minutes.

        Args:
            minutes: Window length in minutes (capped to 60).
            domains: Optional domain filter (comma-separated, e.g., "switch,climate").

        Returns:
            JSON string with:
            - total_changes
            - changes[]: {timestamp, entity_id, from, to}
            - most_active_entities[]
        """
        try:
            return _do_get_recent_state_changes(minutes, domains, ha_url, ha_token)
        except Exception as e:
            return _error_response(str(e))

"""
History Analysis Tools (P1 - Important).

Provides tools for analyzing entity history:
- get_entity_state_history_summary(entity_id, hours_back)
- get_recent_state_changes(minutes, domains)
"""

import hashlib
import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from tools.utils import _build_history_url, _error_response, _success_response, make_ha_request

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"

# =============================================================================
# CONFIG
# =============================================================================

MAX_HISTORY_HOURS: int = 168
MAX_RECENT_MINUTES: int = 60
MAX_RETURNED_CHANGES: int = 50
MAX_BREAKDOWN_STATES: int = 32
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
        parsed = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _calculate_duration(start: datetime, end: datetime) -> float:
    """Calculate duration in minutes."""
    return (end - start).total_seconds() / 60


# =============================================================================
# EXTRACTED LOGIC
# =============================================================================


def _do_get_entity_state_history_summary(
    entity_id: str,
    hours_back: int,
    ha_url: str,
    ha_token: str,
    group_by: str | None = None,
    detail_level: str = "full",
    compact: bool = False,
) -> str:
    """Summarize entity state history instead of returning raw rows.

    Args:
        entity_id: Entity identifier.
        hours_back: Number of hours to analyze (capped to 168).
        ha_url: Home Assistant API URL.
        ha_token: Authorization token.
        group_by: Optional aggregation — ``"hour"`` or ``"day"`` to return
            grouped statistics instead of raw change entries.
        detail_level: "summary" (minimal_response=True, fewer attributes) or
            "full" (default, full attribute data).
        compact: If True, force minimal_response=True to reduce API payload
            (same effect as detail_level="summary"). Default False.
    """
    if detail_level not in ("summary", "full"):
        return _error_response(
            f"Invalid detail_level '{detail_level}'. Must be 'summary' or 'full'."
        )

    hours_back = min(max(int(hours_back), 1), MAX_HISTORY_HOURS)

    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(hours=hours_back)

    url = _build_history_url(
        start_time, entity_id=entity_id, minimal=(detail_level == "summary" or compact)
    )
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
                "recorder_coverage": {
                    "source": "HA_RECORDER_HISTORY_API",
                    "status": "UNVERIFIED",
                    "reason": "Missing recorder rows do not prove absence of events",
                    "requested_start_utc": start_time.isoformat(),
                    "requested_end_utc": end_time.isoformat(),
                    "observed_rows": 0,
                    "duplicate_rows": 0,
                    "observed_start_utc": None,
                    "observed_end_utc": None,
                    "reason_code": "NO_SAMPLES_IN_WINDOW",
                    "entity_registration": "UNVERIFIED",
                    "unknown_unavailable_retained": True,
                },
            }
        )

    changes = history_data[0]

    # Recorder rows are not guaranteed to be ordered or unique. Normalize once
    # before calculating continuity, durations, or observed window bounds.
    parsed_rows: list[tuple[datetime, dict[str, Any]]] = []
    invalid_timestamp_count = 0
    duplicate_rows = 0
    seen_rows: set[tuple[str, Any]] = set()
    for state in changes:
        current_time = _parse_timestamp(state.get("last_changed"))
        if current_time is None:
            invalid_timestamp_count += 1
            continue
        key = (current_time.isoformat(), state.get("state"))
        if key in seen_rows:
            duplicate_rows += 1
            continue
        seen_rows.add(key)
        parsed_rows.append((current_time, state))
    parsed_rows.sort(key=lambda row: row[0])
    changes = [state for _, state in parsed_rows]

    states_stats = defaultdict(lambda: {"count": 0, "total_duration_min": 0.0})  # type: ignore[var-annotated]
    anomalies = []  # type: ignore[var-annotated]
    processed_changes = []

    last_change_time = None
    missing_state_rows = 0
    current_state_duration = 0.0
    rapid_changes = 0

    for i, state in enumerate(changes):
        state_val = state.get("state")
        if not isinstance(state_val, str) or not state_val:
            missing_state_rows += 1
            last_change_time = None
            continue
        # Unavailable/unknown are observed states, not noise to drop.
        current_time = _parse_timestamp(state.get("last_changed"))
        if not current_time:
            continue

        if i < len(changes) - 1:
            next_time = _parse_timestamp(changes[i + 1].get("last_changed"))
        else:
            next_time = end_time
        # The Recorder may prepend a state from before the requested window.
        interval_start = max(current_time, start_time)
        interval_end = min(next_time, end_time) if next_time else interval_start
        duration = max(0.0, _calculate_duration(interval_start, interval_end))
        if i == len(changes) - 1:
            current_state_duration = round(duration, 1)

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

    # Cap numeric-sensor state cardinality, reporting any omitted labels.
    # Failure states must remain visible even when numerical values flood the bounded result.
    sorted_states = sorted(
        states_stats.items(),
        key=lambda item: (
            0 if item[0] in ("unavailable", "unknown") else 1,
            -item[1]["count"],
            str(item[0]),
        ),
    )
    omitted_states = max(0, len(sorted_states) - MAX_BREAKDOWN_STATES)
    final_stats = {}
    for state, stats in sorted_states[:MAX_BREAKDOWN_STATES]:
        avg = stats["total_duration_min"] / max(stats["count"], 1)
        final_stats[state] = {
            "count": stats["count"],
            "total_duration_min": round(stats["total_duration_min"], 1),
            "avg_duration_min": round(avg, 1),
            "percentage": round((stats["total_duration_min"] / (hours_back * 60)) * 100, 1),
        }

    grouped: dict[str, dict[str, float | int]] | None = None
    grouped_utc: dict[str, dict[str, float | int]] | None = None
    if group_by in ("hour", "day") and processed_changes:
        source_buckets: dict[str, list[float]] = {}
        utc_buckets: dict[str, list[float]] = {}
        key_format = "%Y-%m-%dT%H:00" if group_by == "hour" else "%Y-%m-%d"
        utc_key_format = f"{key_format}Z" if group_by == "hour" else key_format
        for ch in processed_changes:
            ts = _parse_timestamp(ch["changed"])
            if not ts:
                continue
            state_val = ch["state"]
            try:
                numeric_val = float(state_val)
            except (ValueError, TypeError):
                continue
            source_buckets.setdefault(ts.strftime(key_format), []).append(numeric_val)
            utc_key = ts.astimezone(UTC).strftime(utc_key_format)
            utc_buckets.setdefault(utc_key, []).append(numeric_val)

        grouped = _summarize_buckets(source_buckets)
        grouped_utc = _summarize_buckets(utc_buckets)

    response_data: dict[str, Any] = {
        "entity_id": entity_id,
        "period_hours": hours_back,
        "total_changes": len(processed_changes),
        "states_breakdown": final_stats,
        "anomalies": anomalies,
        "recorder_coverage": {
            "source": "HA_RECORDER_HISTORY_API",
            "status": "UNVERIFIED",
            "reason": "Recorder rows do not prove full event-bus or window coverage",
            "requested_start_utc": start_time.isoformat(),
            "requested_end_utc": end_time.isoformat(),
            "observed_rows": len(changes),
            "duplicate_rows": duplicate_rows,
            "invalid_timestamps": invalid_timestamp_count,
            "missing_state_rows": missing_state_rows,
            "observed_start_utc": parsed_rows[0][0].isoformat() if parsed_rows else None,
            "observed_end_utc": parsed_rows[-1][0].isoformat() if parsed_rows else None,
            "unknown_unavailable_retained": True,
            "state_breakdown_truncated": omitted_states > 0,
            "omitted_state_labels": omitted_states,
        },
        "current_state_duration_min": current_state_duration,
    }
    if grouped is not None:
        response_data["grouped_by"] = group_by
        response_data["grouped"] = grouped
        response_data["grouped_utc"] = grouped_utc
    else:
        response_data["last_5_changes"] = processed_changes[-5:]

    return _success_response(response_data)


def _summarize_buckets(buckets: dict[str, list[float]]) -> dict[str, dict[str, float | int]]:
    """Report numeric statistics for each time bucket."""
    return {
        bucket: {
            "count": len(values),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "avg": round(sum(values) / len(values), 3),
        }
        for bucket, values in sorted(buckets.items())
    }


def _do_get_recent_state_changes(
    minutes: int,
    domains: str | None,
    ha_url: str,
    ha_token: str,
    entity_ids: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    limit: int = MAX_RETURNED_CHANGES,
) -> str:
    """Retrieve a bounded state-change timeline with explicit coverage.

    ``minutes`` remains the backward-compatible relative window. Callers that
    need reproducibility provide ``start_utc`` and ``end_utc``; the result then
    reports the requested and actually covered UTC interval instead of claiming
    complete Recorder retention.
    """
    minutes = min(max(int(minutes), 1), MAX_RECENT_MINUTES)
    limit = min(max(int(limit), 1), 200)

    if start_utc is not None or end_utc is not None:
        requested_start = _parse_timestamp(start_utc or "")
        requested_end = _parse_timestamp(end_utc or "") if end_utc else datetime.now(UTC)
        if requested_start is None or requested_end is None:
            return _error_response("start_utc and end_utc must be timezone-aware ISO timestamps")
        if requested_end < requested_start:
            return _error_response("end_utc must be >= start_utc")
        start_time = requested_start.astimezone(UTC)
        end_time = requested_end.astimezone(UTC)
        period_minutes = round((end_time - start_time).total_seconds() / 60, 3)
    else:
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=minutes)
        period_minutes = minutes

    url = _build_history_url(start_time, entity_id=entity_ids, minimal=False)
    url += f"&{urlencode({'end_time': end_time.isoformat()})}"
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
            if ts > end_time:
                continue

            changes_list.append(
                {
                    "timestamp": state.get("last_changed"),
                    "utc_timestamp": ts.astimezone(UTC).isoformat(),
                    "source_timestamp": state.get("last_changed"),
                    "entity_id": entity_id,
                    "to_state": state.get("state"),
                    "from_state": prev_state,
                    "user_id": state.get("context", {}).get("user_id"),
                }
            )

            entity_counts[entity_id] += 1
            prev_state = state.get("state")

    changes_list.sort(key=lambda x: x["utc_timestamp"], reverse=True)
    covered = sorted(item["utc_timestamp"] for item in changes_list)
    evidence = json.dumps(
        {
            "entity_ids": entity_ids,
            "requested": [start_time.isoformat(), end_time.isoformat()],
            "changes": changes_list,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    response = {
        "period_minutes": period_minutes,
        "total_changes": len(changes_list),
        "most_active_entities": [
            {"entity_id": e, "changes": c} for e, c in entity_counts.most_common(5)
        ],
        "changes": changes_list[:limit],
        "requested_utc_window": {
            "start_utc": start_time.isoformat(),
            "end_utc": end_time.isoformat(),
            "source": "HA_RECORDER_HISTORY_API",
        },
        "covered_utc_window": {
            "start_utc": covered[0] if covered else None,
            "end_utc": covered[-1] if covered else None,
            "observed_rows": len(changes_list),
            "completeness": "PARTIAL",
            "retention_coverage": "UNKNOWN",
            "recorder_generation": "UNKNOWN",
            "ha_boot_generation": "UNKNOWN",
        },
        "event_classes": {
            "state_transitions": "OBSERVED" if changes_list else "NO_SAMPLES_IN_WINDOW",
            "transient_events": "EVENT_BUS_NOT_PERSISTED",
            "automation_trace": "TRACE_UNAVAILABLE",
        },
        "continuation": {
            "truncated": len(changes_list) > limit,
            "limit": limit,
            "next_cursor": changes_list[limit - 1]["utc_timestamp"]
            if len(changes_list) > limit
            else None,
        },
        "evidence_digest": "sha256:" + hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
    }

    if len(changes_list) > limit:
        response["note"] = f"Showing {limit} of {len(changes_list)} changes"

    return _success_response(response)


def register_history_tools(mcp, ha_url: str, ha_token: str) -> None:  # type: ignore[no-untyped-def]
    """Register history analysis tools on the MCP server."""

    @mcp.tool()
    async def get_entity_state_history_summary(
        entity_id: str,
        hours_back: int = 24,
        group_by: str | None = None,
        detail_level: str = "full",
        compact: bool = False,
    ) -> str:
        """[READ] Summarize entity state history instead of returning raw rows.

        Args:
            entity_id: Entity identifier (e.g., ``switch.test``).
            hours_back: Number of hours to analyze (capped to 168).
            group_by: Optional aggregation level — ``"hour"`` or ``"day"``.
                When set, returns grouped statistics (count, min, max, avg)
                instead of raw change entries (default: None).
            detail_level: "summary" (minimal_response, fewer attributes) or
                "full" (default, complete attribute data).
            compact: If True, force minimal_response=True to reduce API payload
                (same effect as detail_level="summary"). Default False.

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
                entity_id, hours_back, ha_url, ha_token, group_by, detail_level, compact
            )
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_recent_state_changes(
        minutes: int = 10,
        domains: str | None = None,
        entity_ids: str | None = None,
        start_utc: str | None = None,
        end_utc: str | None = None,
        limit: int = MAX_RETURNED_CHANGES,
    ) -> str:
        """[READ] Retrieve a bounded state-change timeline with coverage.

        Args:
            minutes: Relative window length in minutes (capped to 60).
            domains: Optional domain filter (comma-separated, e.g. "switch,climate").
            entity_ids: Optional comma-separated exact entity selector.
            start_utc: Optional timezone-aware ISO start for an exact UTC window.
            end_utc: Optional timezone-aware ISO end; defaults to now when omitted.
            limit: Maximum returned events (1-200, default 50).

        Returns:
            JSON string with total_changes, bounded changes, requested/covered
            UTC windows, Recorder completeness, event-class capability status,
            continuation metadata and a stable evidence digest.
        """
        try:
            return _do_get_recent_state_changes(
                minutes,
                domains,
                ha_url,
                ha_token,
                entity_ids=entity_ids,
                start_utc=start_utc,
                end_utc=end_utc,
                limit=limit,
            )
        except Exception as e:
            return _error_response(str(e))

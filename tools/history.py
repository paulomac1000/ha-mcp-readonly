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

from tools.history_cursor import (
    CursorError,
    HistoryCursorRequest,
    cursor_request,
    decode_cursor,
    history_sort_key,
    issue_cursor,
)
from tools.utils import _build_history_url, _error_response, _success_response, make_ha_request

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"

# =============================================================================
# CONFIG
# =============================================================================

MAX_HISTORY_HOURS: int = 168
MAX_RECENT_MINUTES: int = 60
MAX_RETURNED_CHANGES: int = 50
MAX_CURSOR_ENTITIES: int = 25
MAX_EXACT_WINDOW_MINUTES: int = 1440
MAX_HISTORY_EVENTS: int = 5000
MAX_HISTORY_PAGE_BYTES: int = 2097152
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
    cursor: str | None = None,
) -> str:
    """Retrieve a bounded, cursor-qualified state-change timeline.

    A continuation cursor freezes the exact entity/domain selection and UTC
    window. Rows are normalized and deterministically ordered before slicing,
    so equal timestamps cannot create gaps or duplicates between pages.
    Recorder retention and current registry state remain explicit unknowns
    unless a supported source proves them.
    """
    minutes = min(max(int(minutes), 1), MAX_RECENT_MINUTES)
    limit = min(max(int(limit), 1), 200)
    secret = hashlib.sha256(ha_token.encode("utf-8")).hexdigest()
    source_fingerprint = hashlib.sha256((ha_url + "\0" + ha_token).encode("utf-8")).hexdigest()

    frozen_request: HistoryCursorRequest | None = None
    cursor_offset = 0
    if cursor is not None:
        try:
            frozen_request, cursor_offset = cursor_request(cursor, secret)
        except CursorError as exc:
            return _error_response({"code": exc.code, "message": str(exc), "retryable": False})
        if entity_ids is not None:
            supplied_entities = tuple(
                sorted({value.strip() for value in entity_ids.split(",") if value.strip()})
            )
            if supplied_entities != frozen_request.entity_ids:
                return _error_response(
                    {
                        "code": "MISMATCH",
                        "message": "cursor entity selection mismatch",
                        "retryable": False,
                    }
                )
        if domains is not None:
            supplied_domains = tuple(
                sorted({value.strip().lower() for value in domains.split(",") if value.strip()})
            )
            if supplied_domains != frozen_request.domains:
                return _error_response(
                    {
                        "code": "MISMATCH",
                        "message": "cursor domain selection mismatch",
                        "retryable": False,
                    }
                )
        if start_utc is not None:
            supplied_start = _parse_timestamp(start_utc)
            if supplied_start is None or supplied_start.astimezone(UTC) != frozen_request.start_utc:
                return _error_response(
                    {
                        "code": "MISMATCH",
                        "message": "cursor start window mismatch",
                        "retryable": False,
                    }
                )
        if end_utc is not None:
            supplied_end = _parse_timestamp(end_utc)
            if supplied_end is None or supplied_end.astimezone(UTC) != frozen_request.end_utc:
                return _error_response(
                    {
                        "code": "MISMATCH",
                        "message": "cursor end window mismatch",
                        "retryable": False,
                    }
                )
        entity_ids = ",".join(frozen_request.entity_ids) or None
        domains = ",".join(frozen_request.domains) or None
        start_time = frozen_request.start_utc
        end_time = frozen_request.end_utc
    elif start_utc is not None or end_utc is not None:
        requested_start = _parse_timestamp(start_utc or "")
        requested_end = _parse_timestamp(end_utc or "") if end_utc else datetime.now(UTC)
        if requested_start is None or requested_end is None:
            return _error_response("start_utc and end_utc must be timezone-aware ISO timestamps")
        if requested_end < requested_start:
            return _error_response("end_utc must be >= start_utc")
        start_time = requested_start.astimezone(UTC)
        end_time = requested_end.astimezone(UTC)
    else:
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=minutes)

    period_minutes = round((end_time - start_time).total_seconds() / 60, 3)
    if period_minutes > MAX_EXACT_WINDOW_MINUTES:
        return _error_response(
            f"History window exceeds the {MAX_EXACT_WINDOW_MINUTES}-minute backend bound"
        )

    selected_entities = (
        tuple(sorted({value.strip() for value in entity_ids.split(",") if value.strip()}))
        if entity_ids
        else ()
    )
    if len(selected_entities) > MAX_CURSOR_ENTITIES:
        return _error_response(
            f"History entity selection exceeds the {MAX_CURSOR_ENTITIES}-entity backend bound"
        )
    domain_list = (
        tuple(sorted({value.strip().lower() for value in domains.split(",") if value.strip()}))
        if domains
        else ()
    )

    url = _build_history_url(start_time, entity_id=entity_ids, minimal=True)
    url += f"&{urlencode({'end_time': end_time.isoformat()})}"
    result = make_ha_request(ha_url, ha_token, url)
    if not result["success"]:
        return _error_response(f"Failed to fetch history: {result.get('error')}")

    history_data = result["data"]
    if not isinstance(history_data, list):
        return _error_response("Malformed Home Assistant history response")
    source_row_count = sum(len(rows) for rows in history_data if isinstance(rows, list))
    if source_row_count > MAX_HISTORY_EVENTS:
        return _error_response(
            f"History response exceeds the {MAX_HISTORY_EVENTS}-event processing bound"
        )

    changes_list: list[dict[str, Any]] = []
    entity_counts: Counter[str] = Counter()
    observed_entities: set[str] = set()
    row_kinds: Counter[str] = Counter()
    original_index = 0

    for entity_history in history_data:
        if not isinstance(entity_history, list) or not entity_history:
            continue
        entity_id = entity_history[0].get("entity_id")
        if not isinstance(entity_id, str):
            row_kinds["malformed"] += 1
            continue
        if domain_list and entity_id.split(".", 1)[0] not in domain_list:
            continue
        if selected_entities and entity_id not in selected_entities:
            continue
        observed_entities.add(entity_id)

        parsed_rows: list[tuple[datetime, int, dict[str, Any]]] = []
        for source_index, state in enumerate(entity_history):
            if not isinstance(state, dict):
                row_kinds["malformed"] += 1
                continue
            raw_timestamp = state.get("last_changed")
            ts = _parse_timestamp(raw_timestamp) if isinstance(raw_timestamp, str) else None
            if ts is None or not isinstance(state.get("state"), str):
                row_kinds["malformed"] += 1
                continue
            parsed_rows.append((ts.astimezone(UTC), source_index, state))
        parsed_rows.sort(key=lambda item: (item[0], item[1]))

        prev_state: str | None = None
        previous_identity: tuple[str, str, str | None] | None = None
        for ts, _source_index, state in parsed_rows:
            to_state = state["state"]
            if ts < start_time:
                prev_state = to_state
                previous_identity = (entity_id, ts.isoformat(), to_state)
                row_kinds["baseline"] += 1
                continue
            if ts > end_time:
                continue
            identity = (entity_id, ts.isoformat(), to_state)
            row_kind = (
                "duplicate"
                if identity == previous_identity
                else ("baseline" if prev_state is None else "transition")
            )
            changes_list.append(
                {
                    "timestamp": state.get("last_changed"),
                    "utc_timestamp": ts.isoformat(),
                    "source_timestamp": state.get("last_changed"),
                    "entity_id": entity_id,
                    "to_state": to_state,
                    "from_state": prev_state,
                    "user_id": state.get("context", {}).get("user_id"),
                    "row_kind": row_kind,
                    "original_index": original_index,
                }
            )
            original_index += 1
            entity_counts[entity_id] += 1
            row_kinds[row_kind] += 1
            prev_state = to_state
            previous_identity = identity

    changes_list.sort(key=history_sort_key)
    for sequence, change in enumerate(changes_list):
        change["sequence"] = sequence
        change.pop("original_index", None)

    covered = sorted(item["utc_timestamp"] for item in changes_list)
    evidence = json.dumps(
        {
            "entity_ids": sorted(observed_entities),
            "domains": list(domain_list),
            "requested": [start_time.isoformat(), end_time.isoformat()],
            "changes": changes_list,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence_digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    evidence_generation = int(evidence_digest[:16], 16)
    request_entities = selected_entities or tuple(sorted(observed_entities))
    if len(request_entities) > MAX_CURSOR_ENTITIES:
        return _error_response(
            f"Resolved history selection exceeds the {MAX_CURSOR_ENTITIES}-entity backend bound"
        )
    request = HistoryCursorRequest(
        entity_ids=request_entities,
        domains=domain_list,
        start_utc=start_time,
        end_utc=end_time,
    )

    if cursor is not None:
        try:
            cursor_offset = decode_cursor(
                cursor,
                request,
                source_fingerprint,
                evidence_generation,
                secret,
            )
        except CursorError as exc:
            return _error_response({"code": exc.code, "message": str(exc), "retryable": False})

    page = changes_list[cursor_offset : cursor_offset + limit]
    while (
        page
        and len(json.dumps(page, separators=(",", ":")).encode("utf-8")) > MAX_HISTORY_PAGE_BYTES
    ):
        page.pop()
    if not page and cursor_offset < len(changes_list):
        return _error_response("A single history row exceeds the response byte budget")
    next_offset = cursor_offset + len(page)
    truncated = next_offset < len(changes_list)
    next_cursor = (
        issue_cursor(request, source_fingerprint, evidence_generation, next_offset, secret)
        if truncated
        else None
    )
    response = {
        "period_minutes": period_minutes,
        "total_changes": len(changes_list),
        "most_active_entities": [
            {"entity_id": entity, "changes": count}
            for entity, count in entity_counts.most_common(5)
        ],
        "changes": page,
        "requested_utc_window": {
            "start_utc": start_time.isoformat(),
            "end_utc": end_time.isoformat(),
            "source": "HA_RECORDER_HISTORY_API",
        },
        "covered_utc_window": {
            "start_utc": covered[0] if covered else None,
            "end_utc": covered[-1] if covered else None,
            "observed_rows": len(changes_list),
            "completeness": "NO_SAMPLES_IN_WINDOW" if not changes_list else "PARTIAL",
            "retention_coverage": "UNKNOWN",
            "recorder_generation": "UNKNOWN",
            "ha_boot_generation": "UNKNOWN",
            "current_state_status": "NOT_QUERIED",
            "last_observed_state": changes_list[0]["to_state"] if changes_list else None,
            "last_observed_state_utc": changes_list[0]["utc_timestamp"] if changes_list else None,
            "registry_status": "NOT_VERIFIED",
        },
        "recorder_qualification": {
            "row_kinds": dict(row_kinds),
            "backend_calls": 1,
            "bounds": {
                "max_entities": MAX_CURSOR_ENTITIES,
                "max_window_minutes": MAX_EXACT_WINDOW_MINUTES,
                "max_events": MAX_HISTORY_EVENTS,
                "max_page_bytes": MAX_HISTORY_PAGE_BYTES,
                "backend_event_limit": "POST_FETCH_FAIL_CLOSED",
            },
            "pagination_semantics": "CLIENT_SIDE_SLICE_OF_BOUND_QUERY",
        },
        "event_classes": {
            "state_transitions": "OBSERVED" if changes_list else "NO_SAMPLES_IN_WINDOW",
            "transient_events": "EVENT_BUS_NOT_PERSISTED",
            "automation_trace": "TRACE_UNAVAILABLE",
        },
        "continuation": {
            "truncated": truncated,
            "limit": limit,
            "offset": cursor_offset,
            "next_cursor": next_cursor,
        },
        "evidence_digest": "sha256:" + evidence_digest,
        "evidence_generation": "sha256:" + evidence_digest,
    }

    if truncated:
        response["note"] = f"Showing {len(page)} of {len(changes_list)} changes"

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
        cursor: str | None = None,
    ) -> str:
        """[READ] Retrieve a bounded state-change timeline with coverage.

        Args:
            minutes: Relative window length in minutes (capped to 60).
            domains: Optional domain filter (comma-separated, e.g. "switch,climate").
            entity_ids: Optional comma-separated exact entity selector.
            start_utc: Optional timezone-aware ISO start for an exact UTC window.
            end_utc: Optional timezone-aware ISO end; defaults to now when omitted.
            limit: Maximum returned events (1-200, default 50).
            cursor: Opaque continuation from the previous exact-window page.

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
                cursor=cursor,
            )
        except Exception as e:
            return _error_response(str(e))

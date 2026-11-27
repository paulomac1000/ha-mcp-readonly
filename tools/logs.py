"""
Log Management Tools
Provides tools for reading, analyzing, and searching Home Assistant logs.
Optimized for AI context windows with intelligent caching and filtering.

Optimizations:
- get_log_insights() now returns affected_entities and affected_automations per pattern
- Better error grouping
- TTL cache
"""

import hashlib
import json
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.utils import _error_response, _success_response, tail_log_file

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"

# Simple in-memory cache for log analysis results
_LOG_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_TTL = 60  # seconds
_CACHE_LOCK = threading.Lock()


def _get_cached_result(key: str) -> Any | None:
    """Returns cached result if valid, None otherwise."""
    with _CACHE_LOCK:
        if key in _LOG_CACHE:
            data, timestamp = _LOG_CACHE[key]
            if time.time() - timestamp < _CACHE_TTL:
                return data
    return None


def _set_cache_result(key: str, data: Any) -> None:
    """Sets cache result with current timestamp."""
    with _CACHE_LOCK:
        _LOG_CACHE[key] = (data, time.time())


# Pre-compiled regex patterns for performance
_TIMESTAMP_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)?")
_COMPONENT_PATTERN = re.compile(r"\[([^\]]+)\]")
_LEVEL_PATTERN = re.compile(r"\b(ERROR|WARNING|INFO|DEBUG)\b")
_ENTITY_PATTERN = re.compile(
    r"\b(sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|"
    r"person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|"
    r"timer|counter|number|select|button|scene|group|alarm_control_panel)\.[a-zA-Z0-9_]+\b"
)
_AUTOMATION_PATTERN = re.compile(r"automation\.([a-zA-Z0-9_]+)")


# ========================================
# INTERNAL HELPERS
# ========================================


def _parse_log_line(line: str) -> dict[str, Any]:
    """Parse a single log line into structured data."""
    result = {"raw": line.strip()}

    # Extract timestamp
    ts_match = _TIMESTAMP_PATTERN.search(line)
    if ts_match:
        result["timestamp"] = ts_match.group(1)

    # Extract level
    level_match = _LEVEL_PATTERN.search(line)
    if level_match:
        result["level"] = level_match.group(1)

    # Extract component
    comp_match = _COMPONENT_PATTERN.search(line)
    if comp_match:
        result["component"] = comp_match.group(1)

    # Extract message (after component)
    if comp_match:
        msg_start = comp_match.end()
        result["message"] = line[msg_start:].strip()
    else:
        result["message"] = line.strip()

    if "timestamp" not in result and "level" not in result:
        result["unparsed"] = True  # type: ignore[assignment]

    return result


_OFFSET_PATTERN = re.compile(r"([+-])(\d{2}):(\d{2})")
_STARTUP_MARKER = "Starting Home Assistant"


def _parse_trusted_timezone(value: str | None) -> tzinfo | None:
    """Parse a caller-supplied IANA timezone or fixed UTC offset."""
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.upper() in {"Z", "UTC"}:
        return UTC
    offset_match = _OFFSET_PATTERN.fullmatch(candidate)
    if offset_match is not None:
        sign = 1 if offset_match.group(1) == "+" else -1
        delta = timedelta(
            hours=int(offset_match.group(2)),
            minutes=int(offset_match.group(3)),
        )
        return timezone(sign * delta)
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _source_time_to_utc(value: str, zone: tzinfo) -> datetime:
    """Convert a naive source timestamp using a trusted timezone."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone).astimezone(UTC)


def _offset_label(value: datetime) -> str:
    """Return a compact +HH:MM offset label for an aware datetime."""
    return value.isoformat()[-6:]


def _local_offset_label(value: str, zone: tzinfo) -> str:
    """Return the source-local offset for a naive timestamp."""
    return _offset_label(datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone))


def _scan_generation_evidence(log_lines: list[str]) -> dict[str, Any]:
    """Derive boot generation and file span without assuming a clean boot."""
    marker_indices = [index for index, line in enumerate(log_lines) if _STARTUP_MARKER in line]
    dated = [
        match.group(1)
        for line in log_lines
        if (match := _TIMESTAMP_PATTERN.search(line)) is not None
    ]
    marker_count = len(marker_indices)
    marker_source_time: str | None = None
    generation: str | None = None
    if marker_indices:
        marker_source_time = _parse_log_line(log_lines[marker_indices[-1]]).get("timestamp")
    if marker_count == 1:
        segment = "".join(log_lines[marker_indices[0] :]).encode("utf-8")
        generation = "sha256:" + hashlib.sha256(segment).hexdigest()
        status = "DERIVED"
    elif marker_count > 1:
        status = "MIXED_GENERATIONS"
    else:
        status = "START_MARKER_MISSING"
    return {
        "status": status,
        "generation": generation,
        "startup_marker_count": marker_count,
        "startup_marker_source_time": marker_source_time,
        "marker_indices": marker_indices,
        "source_file_span": {
            "earliest": min(dated, default=None),
            "latest": max(dated, default=None),
        },
    }


def _evidence_digest(payload: dict[str, Any]) -> str:
    """Hash a stable projection of the evidence receipt, excluding clock churn."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _read_log_file(
    log_file: str, config_path: str, max_lines: int | None = None
) -> tuple[list[str] | None, dict[str, object]]:
    """Read log file efficiently, handling encoding errors.

    Always reads the last ``max_lines`` lines (most recent) via ``tail_log_file``.
    When ``max_lines`` is ``None``, defaults to 10,000.

    Returns:
        Tuple of (lines list or None, _meta dict with ``source``, ``truncated``, ``max_lines``).
    """
    effective_max = 10000 if max_lines is None else max_lines
    log_path = Path(config_path) / log_file
    if not log_path.exists():
        return None, {"source": "log_file", "truncated": False, "max_lines": effective_max}

    try:
        tailed = tail_log_file(str(log_path), lines=effective_max)
        lines = [line + "\n" if not line.endswith("\n") else line for line in tailed]
        truncated = len(tailed) >= effective_max
        return lines, {"source": "log_file", "truncated": truncated, "max_lines": effective_max}
    except Exception:
        return None, {"source": "log_file", "truncated": False, "max_lines": effective_max}


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi_escape_codes(text: str) -> str:
    """Remove ANSI colour escape sequences from Supervisor log output.

    Args:
        text: Raw Supervisor proxy response body.

    Returns:
        Text with all ANSI escape sequences removed.
    """
    return _ANSI_ESCAPE_PATTERN.sub("", text)


def _read_supervisor_core_logs(
    ha_url: str, ha_token: str, max_lines: int | None, previous_boot: bool = False
) -> list[str] | None:
    """Read Home Assistant core logs through the Supervisor proxy.

    HA OS and Supervised installs stopped writing ``home-assistant.log`` in
    Home Assistant 2025.11 (issue #31); the Supervisor proxy serves the same
    records with a plain long-lived access token. Core and Container installs
    have no Supervisor and answer 404, which maps to ``None``.

    Args:
        ha_url: Home Assistant base URL.
        ha_token: Long-lived access token.
        max_lines: Maximum number of trailing lines to return (default 10000).
        previous_boot: Select the previous boot (journal offset ``-1``) instead
            of the current one.

    Returns:
        Log lines terminated by newlines, or ``None`` when the proxy is
        unavailable or the request fails.
    """
    from tools.utils import make_ha_request

    effective_max = 10000 if max_lines is None else max_lines
    if previous_boot:
        endpoint = "/api/hassio/core/logs/boots/-1"
    else:
        endpoint = f"/api/hassio/core/logs?lines={effective_max}"
    result = make_ha_request(ha_url, ha_token, endpoint, timeout=30)
    if not result.get("success") or not isinstance(result.get("data"), str):
        return None
    text = _strip_ansi_escape_codes(result["data"])
    lines = [line + "\n" for line in text.splitlines()]
    return lines[-effective_max:]


def _acquire_log_lines(
    log_file: str,
    config_path: str,
    ha_url: str,
    ha_token: str,
    max_lines: int | None = None,
) -> tuple[list[str] | None, dict[str, object]]:
    """Read log lines from the file, falling back to the Supervisor proxy.

    Order: log file first, so installs that still write it keep working
    unchanged; then the Supervisor proxy for HA OS/Supervised installs whose
    logs live only in the systemd journal.

    Args:
        log_file: Log file name relative to the config directory; any name
            other than ``home-assistant.log`` maps to the previous boot.
        config_path: Home Assistant config directory.
        ha_url: Home Assistant base URL (empty disables the proxy fallback).
        ha_token: Long-lived access token.
        max_lines: Maximum number of trailing lines to return (default 10000).

    Returns:
        Tuple of (lines list or None, metadata dict with ``source``,
        ``truncated``, and ``max_lines``).
    """
    lines, meta = _read_log_file(log_file, config_path, max_lines)
    if lines:
        return lines, meta
    if ha_url and ha_token:
        supervisor_lines = _read_supervisor_core_logs(
            ha_url, ha_token, max_lines, previous_boot=log_file != "home-assistant.log"
        )
        if supervisor_lines:
            effective_max = 10000 if max_lines is None else max_lines
            return supervisor_lines, {
                "source": "supervisor_proxy",
                "truncated": len(supervisor_lines) >= effective_max,
                "max_lines": effective_max,
            }
    return None, meta


def _extract_entities(text: str) -> set[str]:
    """Extract all entity ids from text."""
    return set(_ENTITY_PATTERN.findall(text))


def _extract_full_entity_ids(text: str) -> set[str]:
    """Extract full entity ids (domain.name) from text."""
    pattern = r"\b(?:sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|timer|counter|number|select|button|scene|group|alarm_control_panel)\.[a-zA-Z0-9_]+\b"
    return set(re.findall(pattern, text))


def _extract_automations(text: str) -> set[str]:
    """Extract automation ids from text."""
    matches = _AUTOMATION_PATTERN.findall(text)
    return {f"automation.{m}" for m in matches}


def _normalize_pattern(message: str) -> str:
    """Normalize message for grouping similar errors."""
    normalized = message
    # Remove timestamps
    normalized = re.sub(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?", "TIMESTAMP", normalized
    )
    # Remove Uuids and hex ids
    normalized = re.sub(r"[a-f0-9]{8,}", "ID", normalized)
    # Remove IP addresses
    normalized = re.sub(r"\d+\.\d+\.\d+\.\d+", "IP", normalized)
    # Remove numbers (but keep entity structure)
    normalized = re.sub(r"(?<![a-z_])\d+(?![a-z_])", "N", normalized)
    # Limit length
    return normalized[:150].strip()


def _categorize_error(component: str, message: str) -> str:
    """Categorize error type based on message content."""
    msg_lower = message.lower()

    if "timeout" in msg_lower or "timed out" in msg_lower:
        return "timeout"
    elif "connection" in msg_lower or "connect" in msg_lower or "refused" in msg_lower:
        return "connection"
    elif "unavailable" in msg_lower or "not available" in msg_lower:
        return "unavailable"
    elif "permission" in msg_lower or "access denied" in msg_lower or "unauthorized" in msg_lower:
        return "permission"
    elif "api" in msg_lower or "http" in msg_lower or "429" in msg_lower or "500" in msg_lower:
        return "api"
    elif "template" in msg_lower:
        return "template"
    elif "config" in msg_lower or "configuration" in msg_lower:
        return "configuration"
    elif "yaml" in msg_lower or "syntax" in msg_lower:
        return "syntax"
    else:
        return "other"


# ========================================
# OPTIMIZED LOG ANALYSIS
# ========================================


def _do_get_log_insights(
    hours: int = 1,
    severity: str = "warning",
    group_similar: bool = True,
    include_affected_entities: bool = True,
    max_patterns: int = 10,
    config_path: str = "",
    ha_url: str = "",
    ha_token: str = "",
) -> dict[str, Any]:
    hours = min(max(int(hours), 1), 24)
    log_lines, file_meta = _acquire_log_lines("home-assistant.log", config_path, ha_url, ha_token)
    api_fallback_used = False

    if not log_lines and ha_url and ha_token:
        from tools.utils import make_ha_request

        api_result = make_ha_request(ha_url, ha_token, "/api/error_log")
        if api_result.get("success") and isinstance(api_result.get("data"), str):
            log_lines = api_result["data"].split("\n")
            api_fallback_used = True
        else:
            lb_result = make_ha_request(ha_url, ha_token, "/api/logbook")
            if lb_result.get("success") and isinstance(lb_result.get("data"), list):
                entries = lb_result["data"]
                log_lines = [f"{e.get('when', '')} {e.get('message', '')}\n" for e in entries]
                api_fallback_used = True

    if not log_lines:
        return {"error": "home-assistant.log not found"}

    _meta: dict[str, object] = (
        {"source": "api_fallback", "truncated": False, "max_lines": 10000}
        if api_fallback_used
        else dict(file_meta)
    )
    # The old key ignored the actual log source/target. Two independent HA
    # installations could receive each other's cached diagnostics.
    excerpt_digest = hashlib.sha256("".join(log_lines).encode("utf-8")).hexdigest()
    target_digest = hashlib.sha256((config_path + "\0" + ha_url).encode("utf-8")).hexdigest()
    cache_key = repr(
        (
            "insights",
            target_digest,
            excerpt_digest,
            hours,
            severity,
            group_similar,
            include_affected_entities,
            max_patterns,
            _meta.get("source"),
            bool(_meta.get("truncated")),
            _meta.get("max_lines"),
        )
    )
    cached = _get_cached_result(cache_key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    _meta["excerpt_sha256"] = excerpt_digest
    _meta["boot_generation"] = "UNKNOWN"
    _meta["coverage_status"] = "PARTIAL" if _meta.get("truncated") else "SOURCE_SNAPSHOT_ONLY"

    cutoff_time = datetime.now(UTC) - timedelta(hours=hours)

    # Data structures
    errors: list[dict] = []  # type: ignore[type-arg]
    warnings: list[dict] = []  # type: ignore[type-arg]
    all_entities: set[str] = set()
    all_automations: set[str] = set()
    component_counter: Counter = Counter()  # type: ignore[type-arg]
    category_counter: Counter = Counter()  # type: ignore[type-arg]

    # Pattern grouping with affected entities
    pattern_data: dict[str, dict] = defaultdict(  # type: ignore[type-arg]
        lambda: {
            "count": 0,
            "first_occurrence": None,
            "last_occurrence": None,
            "component": "",
            "category": "",
            "affected_entities": set(),
            "affected_automations": set(),
            "example_message": "",
        }
    )

    for line in log_lines:
        parsed = _parse_log_line(line)

        if parsed.get("unparsed"):
            continue

        timestamp_str = parsed.get("timestamp", "")
        level = parsed.get("level", "")
        component = parsed.get("component", "")
        message = parsed.get("message", "")

        # Filter by severity
        if severity == "error" and level != "ERROR":
            continue
        elif severity == "warning" and level not in ["ERROR", "WARNING"]:
            continue

        # Filter by time
        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            if timestamp < cutoff_time:
                continue
        except (ValueError, TypeError):
            continue

        # Categorize
        category = _categorize_error(component, message)
        category_counter[category] += 1
        component_counter[component] += 1

        # Extract entities and automations
        line_entities = _extract_full_entity_ids(line)
        line_automations = _extract_automations(line)

        if include_affected_entities:
            all_entities.update(line_entities)
            all_automations.update(line_automations)

        entry = {
            "timestamp": timestamp_str,
            "level": level,
            "component": component,
            "message": message[:200],
            "category": category,
        }

        if level == "ERROR":
            errors.append(entry)
        else:
            warnings.append(entry)

        # Group by pattern
        if group_similar:
            pattern_key = _normalize_pattern(message)
            pdata = pattern_data[pattern_key]
            pdata["count"] += 1
            if not pdata["first_occurrence"]:
                pdata["first_occurrence"] = timestamp_str
            pdata["last_occurrence"] = timestamp_str
            pdata["component"] = component
            pdata["category"] = category
            pdata["affected_entities"].update(line_entities)
            pdata["affected_automations"].update(line_automations)
            if not pdata["example_message"]:
                pdata["example_message"] = message[:150]

    # Build grouped errors response
    grouped_errors = {}
    if group_similar:
        sorted_patterns = sorted(pattern_data.items(), key=lambda x: x[1]["count"], reverse=True)[
            :max_patterns
        ]

        for pattern_key, data in sorted_patterns:
            if data["count"] >= 2:  # Only patterns with 2+ occurrences
                grouped_errors[data["example_message"][:80]] = {
                    "count": data["count"],
                    "first_occurrence": data["first_occurrence"],
                    "last_occurrence": data["last_occurrence"],
                    "component": data["component"],
                    "category": data["category"],
                    "affected_entities": list(data["affected_entities"])[:10],
                    "affected_automations": list(data["affected_automations"])[:5],
                }

    # Build result
    result = {
        "success": True,
        "summary": {
            "time_range_hours": hours,
            "total_errors": len(errors),
            "total_warnings": len(warnings),
            "unique_components": len(component_counter),
            "unique_patterns": len([p for p in pattern_data.values() if p["count"] >= 2]),
            "affected_entities_count": len(all_entities) if include_affected_entities else None,
            "affected_automations_count": len(all_automations)
            if include_affected_entities
            else None,
        },
        "grouped_errors": grouped_errors if group_similar else {},
        "error_categories": dict(category_counter.most_common()),
        "affected_components": [
            {"component": comp, "count": count} for comp, count in component_counter.most_common(15)
        ],
        "affected_entities": sorted(list(all_entities))[:30] if include_affected_entities else [],
        "affected_automations": sorted(list(all_automations))[:10]
        if include_affected_entities
        else [],
        "recent_errors": errors[-5:],
        "recent_warnings": warnings[-5:],
        "recommendations": [],
    }

    # Generate recommendations
    if category_counter["timeout"] > 5:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "high",
                "category": "timeout",
                "message": f"High timeout count ({category_counter['timeout']}). Check network connectivity and device responsiveness.",
            }
        )

    if category_counter["unavailable"] > 10:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "high",
                "category": "unavailable",
                "message": f"Many unavailable entities ({category_counter['unavailable']}). Check integrations.",
            }
        )

    if category_counter["template"] > 3:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "category": "template",
                "message": f"Template errors detected ({category_counter['template']}). Use Developer Tools to debug.",
            }
        )

    if category_counter["api"] > 5:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "category": "api",
                "message": f"API errors detected ({category_counter['api']}). Check external service status and rate limits.",
            }
        )

    top_component = component_counter.most_common(1)
    if top_component and top_component[0][1] > 10:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "high",
                "category": "component",
                "message": f"Component '{top_component[0][0]}' generates most errors ({top_component[0][1]}). Investigate this integration.",
            }
        )

    # Check for specific automation issues
    if all_automations:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "category": "automation",
                "message": f"Automations with issues: {', '.join(list(all_automations)[:3])}",
            }
        )

    if not result["recommendations"]:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "info",
                "category": "general",
                "message": "No critical issues detected in this time period.",
            }
        )

    result["count_semantics"] = {
        "version": 1,
        "raw": "SEVERITY_PREFIX",
        "grouped": "NORMALIZED_SIGNATURE",
    }
    result["raw_level_counts"] = {"ERROR": len(errors), "WARNING": len(warnings)}
    result["requested_utc_window"] = {
        "status": "UNVERIFIED",
        "reason": "Source timestamps have no trusted UTC offset",
    }
    _meta["cutoff_reason"] = "TRUNCATED_MAX_LINES" if _meta.get("truncated") else "NONE"
    result["_meta"] = _meta
    _set_cache_result(cache_key, result)
    return result


def _do_analyze_log_errors(
    log_source: str = "current",
    max_results: int = 50,
    config_path: str = "",
    ha_url: str = "",
    ha_token: str = "",
    since_source_time: str | None = None,
    until_source_time: str | None = None,
    source_timezone: str | None = None,
    owner_component: str | None = None,
) -> dict[str, Any]:
    """Summarize an observed log excerpt without claiming full-boot coverage.

    Optional boundaries use the source log's naive wall-clock timestamp, NOT
    UTC. Exact UTC windows are produced only when the caller supplies a trusted
    source timezone or fixed offset.
    """
    if log_source not in {"current", "previous"}:
        return {"error": "log_source must be 'current' or 'previous'"}
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        return {"error": "max_results must be a positive integer"}
    if max_results < 1:
        return {"error": "max_results must be a positive integer"}
    max_results = min(max_results, 20)

    for value in (since_source_time, until_source_time):
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value
            ):
                return {"error": "window bounds must use YYYY-MM-DD HH:MM:SS (source local)"}
            try:
                datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return {"error": "invalid source-local timestamp"}
    if since_source_time and until_source_time and since_source_time > until_source_time:
        return {"error": "since_source_time must be <= until_source_time"}

    trusted_tz = _parse_trusted_timezone(source_timezone)
    if source_timezone is not None and trusted_tz is None:
        return {"error": "source_timezone must be a valid IANA name or +HH:MM offset"}

    log_file = "home-assistant.log" if log_source == "current" else "home-assistant.log.1"
    log_lines, source_meta = _acquire_log_lines(log_file, config_path, ha_url, ha_token)
    if not log_lines:
        return {
            "error": (
                f"{log_file} not found and the Supervisor log proxy "
                "(api/hassio/core/logs) is unavailable"
            )
        }

    # Cache by the bounded *actual content* and target identity. The prior
    # cache key ignored file/target changes and could replay another source's
    # counts for 60 seconds, even if the caller requested a different HA.
    excerpt_digest = hashlib.sha256("".join(log_lines).encode("utf-8")).hexdigest()
    target_digest = hashlib.sha256((config_path + "\0" + ha_url).encode("utf-8")).hexdigest()
    cache_key = repr(
        (
            "analyze",
            target_digest,
            log_source,
            max_results,
            since_source_time,
            until_source_time,
            source_timezone,
            owner_component,
            excerpt_digest,
            source_meta.get("source"),
            bool(source_meta.get("truncated")),
            source_meta.get("max_lines"),
        )
    )
    observed_at = datetime.now(UTC).isoformat()
    cached = _get_cached_result(cache_key)
    if cached is not None:
        return {**cached, "replay_status": "UNCHANGED"}

    generation_evidence = _scan_generation_evidence(log_lines)
    marker_indices = generation_evidence["marker_indices"]
    current_generation_start = marker_indices[-1] if marker_indices else None
    critical_error_count = 0
    owner_level_counts: Counter[str] = Counter()
    sightings: list[tuple[str, str]] = []

    errors = []
    warnings = []
    tracebacks = []
    device_issues = defaultdict(list)
    permission_errors = []

    current_traceback: list[str] = []
    in_traceback = False
    has_window = since_source_time is not None or until_source_time is not None
    last_dated_in_window = not has_window
    seen_timestamps: list[str] = []
    undated_or_unparsed = 0
    omitted_outside_window = 0
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}

    for line_index, line in enumerate(log_lines):
        parsed = _parse_log_line(line)
        ts = parsed.get("timestamp")
        if (
            current_generation_start is not None
            and line_index >= current_generation_start
            and parsed.get("level") == "ERROR"
        ):
            critical_error_count += 1
        if isinstance(ts, str):
            last_dated_in_window = (since_source_time is None or ts >= since_source_time) and (
                until_source_time is None or ts <= until_source_time
            )
            if last_dated_in_window:
                seen_timestamps.append(ts)
            if not last_dated_in_window and in_traceback:
                in_traceback = False
                current_traceback = []
        elif has_window and not last_dated_in_window:
            undated_or_unparsed += 1
        if has_window and not last_dated_in_window:
            omitted_outside_window += 1
            continue
        if not isinstance(ts, str):
            undated_or_unparsed += int(not has_window)

        # Detect traceback
        if "Traceback (most recent call last):" in line:
            in_traceback = True
            current_traceback = [line]
            continue

        if in_traceback:
            current_traceback.append(line)
            if "Error:" in line or "Exception:" in line:
                tracebacks.append(
                    {
                        "error_type": line.strip().split(":")[0].strip(),
                        "traceback_preview": "".join(current_traceback[-5:])[:500],
                    }
                )
                in_traceback = False
                current_traceback = []
            continue

        if parsed.get("unparsed"):
            continue

        level = parsed.get("level", "")
        component = parsed.get("component", "")
        message = parsed.get("message", "")

        if level in {"ERROR", "WARNING"}:
            if isinstance(ts, str):
                sightings.append((ts, level))
            if owner_component and owner_component.lower() in str(component).lower():
                owner_level_counts[level] += 1

            key = (level, str(component), _normalize_pattern(str(message)))
            entry = grouped.setdefault(
                key,
                {
                    "severity": level,
                    "component": component,
                    "pattern": key[2],
                    "count": 0,
                    "first_source_time": None,
                    "last_source_time": None,
                },
            )
            entry["count"] += 1
            if isinstance(ts, str):
                if entry["first_source_time"] is None or ts < entry["first_source_time"]:
                    entry["first_source_time"] = ts
                if entry["last_source_time"] is None or ts > entry["last_source_time"]:
                    entry["last_source_time"] = ts

        if level == "ERROR":
            errors.append(parsed)

            if "PermissionError" in message or "Access denied" in message:
                permission_errors.append({"component": component, "message": message[:200]})

        elif level == "WARNING":
            warnings.append(parsed)

        if "Unable to discover" in message or "Failed to refresh" in message:
            device_match = re.search(r"([\w\s-]+):", message)
            if device_match:
                device_name = device_match.group(1).strip()
                device_issues[device_name].append(
                    {
                        "timestamp": parsed.get("timestamp"),
                        "message": message[:150],
                    }
                )

    # Aggregate
    error_counter = Counter([e.get("message", "")[:200] for e in errors])
    error_comp_counter = Counter([e.get("component", "") for e in errors])
    warning_comp_counter = Counter([w.get("component", "") for w in warnings])

    source_truncated = bool(source_meta.get("truncated"))
    marker_count = int(generation_evidence["startup_marker_count"])
    generation_status = str(generation_evidence["status"])
    generation = generation_evidence["generation"]
    sightings.sort(key=lambda item: item[0])
    first_source_time = sightings[0][0] if sightings else None
    last_source_time = sightings[-1][0] if sightings else None
    generation_scope = "SINGLE" if marker_count == 1 else "MIXED" if marker_count > 1 else "UNKNOWN"
    first_sighting = {
        "source_time": first_source_time,
        "utc": _source_time_to_utc(first_source_time, trusted_tz).isoformat()
        if trusted_tz is not None and first_source_time is not None
        else None,
        "generation_scope": generation_scope,
    }
    last_sighting = {
        "source_time": last_source_time,
        "utc": _source_time_to_utc(last_source_time, trusted_tz).isoformat()
        if trusted_tz is not None and last_source_time is not None
        else None,
        "generation_scope": generation_scope,
    }
    if trusted_tz is not None and first_source_time is not None:
        local_offset = _local_offset_label(first_source_time, trusted_tz)
    elif trusted_tz is not None and since_source_time is not None:
        local_offset = _local_offset_label(since_source_time, trusted_tz)
    else:
        local_offset = None
    if trusted_tz is not None and (since_source_time is not None or until_source_time is not None):
        requested_utc_window = {
            "status": "VERIFIED",
            "since_utc": _source_time_to_utc(since_source_time, trusted_tz).isoformat()
            if since_source_time is not None
            else None,
            "until_utc": _source_time_to_utc(until_source_time, trusted_tz).isoformat()
            if until_source_time is not None
            else None,
        }
    elif trusted_tz is not None:
        requested_utc_window = {"status": "NOT_REQUESTED", "since_utc": None, "until_utc": None}
    else:
        requested_utc_window = {
            "status": "UNVERIFIED",
            "since_utc": None,
            "until_utc": None,
            "reason": "Source timestamps have no trusted UTC offset",
        }
    if trusted_tz is not None and sightings:
        covered_utc_window = {
            "status": "VERIFIED",
            "first_seen_utc": _source_time_to_utc(first_source_time, trusted_tz).isoformat()
            if first_source_time is not None
            else None,
            "last_seen_utc": _source_time_to_utc(last_source_time, trusted_tz).isoformat()
            if last_source_time is not None
            else None,
        }
    else:
        covered_utc_window = {
            "status": "UNVERIFIED",
            "first_seen_utc": None,
            "last_seen_utc": None,
            "reason": "Source timestamps have no trusted UTC offset"
            if trusted_tz is None
            else "No dated error or warning sightings in the selected window",
        }
    if log_source == "previous":
        rotation_coverage = "LOG_ROTATED"
    elif marker_count == 0:
        rotation_coverage = "UNKNOWN"
    elif marker_count > 1:
        rotation_coverage = "MIXED_GENERATIONS"
    else:
        rotation_coverage = "SINGLE_GENERATION"
    if source_truncated:
        cutoff_reason = "TRUNCATED_MAX_LINES"
    elif log_source == "previous":
        cutoff_reason = "ROTATION"
    elif marker_count == 0:
        cutoff_reason = "UNKNOWN"
    else:
        cutoff_reason = "NONE"
    if marker_count == 0:
        global_critical: bool | None = None
        critical_reason = "START_MARKER_MISSING"
    elif source_truncated:
        global_critical = None
        critical_reason = "COVERAGE_PARTIAL"
    elif marker_count > 1:
        global_critical = None
        critical_reason = "MIXED_GENERATIONS"
    else:
        global_critical = critical_error_count > 0
        critical_reason = None
    if owner_component:
        owner_counts = {
            "ERROR": owner_level_counts.get("ERROR", 0),
            "WARNING": owner_level_counts.get("WARNING", 0),
        }
        owner_outcome = (
            "OWNER_ERRORS_OBSERVED"
            if owner_counts["ERROR"] or owner_counts["WARNING"]
            else "OWNER_WINDOW_NO_ERRORS"
        )
    else:
        owner_counts = None
        owner_outcome = "NOT_REQUESTED"
    window_exceeds_observed = has_window and (
        not seen_timestamps
        or (since_source_time is not None and since_source_time < min(seen_timestamps))
        or (until_source_time is not None and until_source_time > max(seen_timestamps))
    )
    source_coverage = (
        "PARTIAL"
        if source_truncated or undated_or_unparsed or marker_count != 1 or log_source == "previous"
        else "WINDOW_UNVERIFIED"
        if window_exceeds_observed
        else "SOURCE_SNAPSHOT_ONLY"
    )
    summary = {
        "success": True,
        "log_source": log_file,
        "analysis_outcome": "ERRORS_OBSERVED" if errors else "NO_ERRORS_OBSERVED_ONLY",
        "coverage_status": source_coverage,
        "source_observed_at_utc": observed_at,
        "source_metadata": {
            "backend": source_meta.get("source", "unknown"),
            "truncated": source_truncated,
            "max_lines": source_meta.get("max_lines"),
            "observed_lines": len(log_lines),
            "excerpt_sha256": excerpt_digest,
            "boot_generation": generation if isinstance(generation, str) else "UNKNOWN",
        },
        "requested_window": {
            "since": since_source_time,
            "until": until_source_time,
            "time_basis": "CALLER_TRUSTED_OFFSET"
            if trusted_tz is not None
            else "SOURCE_LOG_LOCAL_UNVERIFIED",
        },
        "time_basis": {
            "clock_confidence": "CALLER_TRUSTED_OFFSET"
            if trusted_tz is not None
            else "TIME_BASIS_UNKNOWN",
            "timezone_source": "CALLER" if trusted_tz is not None else "UNKNOWN",
            "source_timezone": source_timezone,
            "local_offset": local_offset,
        },
        "requested_utc_window": requested_utc_window,
        "covered_utc_window": covered_utc_window,
        "observed_utc_window": covered_utc_window,
        "first_sighting": first_sighting,
        "last_sighting": last_sighting,
        "source_file_span": generation_evidence["source_file_span"],
        "ha_start_generation": {
            "status": generation_status,
            "generation": generation,
            "startup_marker_count": marker_count,
            "startup_marker_source_time": generation_evidence["startup_marker_source_time"],
        },
        "rotation_coverage": rotation_coverage,
        "cutoff_reason": cutoff_reason,
        "owner_receipt": {
            "owner_scope": owner_component,
            "owner_outcome": owner_outcome,
            "owner_raw_level_counts": owner_counts,
            "global_critical_startup_errors_present": global_critical,
            "global_critical_error_count": critical_error_count if marker_count > 0 else 0,
            "reason": critical_reason,
        },
        "count_semantics": {
            "version": 1,
            "raw": "SEVERITY_PREFIX",
            "grouped": "NORMALIZED_SIGNATURE",
        },
        "observed_source_window": {
            "earliest": min(seen_timestamps, default=None),
            "latest": max(seen_timestamps, default=None),
            "excluded_lines": omitted_outside_window,
            "undated_lines": undated_or_unparsed,
        },
        "raw_level_counts": {
            "ERROR": len(errors),
            "WARNING": len(warnings),
        },
        "grouped_signatures": sorted(
            grouped.values(),
            key=lambda e: (
                -e["count"],
                e["severity"],
                e["component"],
                e["pattern"],
            ),
        )[:max_results],
        "grouped_signatures_omitted": max(0, len(grouped) - max_results),
        "total_errors": len(errors),
        "total_warnings": len(warnings),
        "total_tracebacks": len(tracebacks),
        "most_common_errors": [
            {"message": msg, "count": count}
            for msg, count in error_counter.most_common(max_results)
        ],
        "components_with_errors": [
            {"component": comp, "count": count}
            for comp, count in error_comp_counter.most_common(20)
        ],
        "components_with_warnings": [
            {"component": comp, "count": count}
            for comp, count in warning_comp_counter.most_common(20)
        ],
        "permission_errors": permission_errors[:10],
        "device_issues": {
            device: issues[:3] for device, issues in list(device_issues.items())[:10]
        },
        "recent_tracebacks": tracebacks[-5:],
        "recent_errors": [
            {
                "timestamp": e.get("timestamp"),
                "component": e.get("component"),
                "message": e.get("message", "")[:200],
            }
            for e in errors[-10:]
        ],
    }

    summary["replay_status"] = "OBSERVED"
    summary["evidence_digest"] = _evidence_digest(
        {
            "backend": source_meta.get("source", "unknown"),
            "log_source": log_file,
            "excerpt_sha256": excerpt_digest,
            "generation_status": generation_status,
            "generation": generation,
            "rotation_coverage": rotation_coverage,
            "cutoff_reason": cutoff_reason,
            "requested": [since_source_time, until_source_time, source_timezone],
            "raw_level_counts": summary["raw_level_counts"],
            "owner_scope": owner_component,
            "owner_counts": owner_counts,
            "grouped": [
                [item["severity"], item["component"], item["pattern"], item["count"]]
                for item in summary["grouped_signatures"]
            ],
        }
    )
    _set_cache_result(cache_key, summary)
    return summary


# ========================================
# BASIC LOG READING
# ========================================


def _do_get_recent_logs(
    lines: int = 100,
    level: str = "all",
    config_path: str = "",
    ha_url: str = "",
    ha_token: str = "",
) -> dict[str, Any]:
    lines = min(int(lines), 500)
    log_lines, log_meta = _acquire_log_lines(
        "home-assistant.log", config_path, ha_url, ha_token, lines * 2
    )

    if not log_lines:
        return {"error": "home-assistant.log not found and the Supervisor log proxy is unavailable"}

    if level.lower() != "all":
        level_upper = level.upper()
        log_lines = [line for line in log_lines if level_upper in line]

    result_lines = log_lines[-lines:]
    return {
        "lines_requested": lines,
        "lines_returned": len(result_lines),
        "level_filter": level,
        "source": log_meta.get("source"),
        "logs": "".join(result_lines),
    }


def _do_get_previous_logs(
    lines: int = 100,
    level: str = "all",
    config_path: str = "",
    ha_url: str = "",
    ha_token: str = "",
) -> dict[str, Any]:
    lines = min(int(lines), 500)
    log_lines, log_meta = _acquire_log_lines(
        "home-assistant.log.1", config_path, ha_url, ha_token, lines * 2
    )

    if not log_lines:
        return {
            "error": "home-assistant.log.1 not found and the previous-boot Supervisor "
            "log proxy (/api/hassio/core/logs/boots/-1) is unavailable"
        }

    if level.lower() != "all":
        level_upper = level.upper()
        log_lines = [line for line in log_lines if level_upper in line]

    result_lines = log_lines[-lines:]
    return {
        "lines_requested": lines,
        "lines_returned": len(result_lines),
        "level_filter": level,
        "source": log_meta.get("source"),
        "logs": "".join(result_lines),
    }


# ========================================
# LOG SEARCH
# ========================================


def _do_search_logs(
    search_term: str,
    log_source: str = "current",
    max_results: int = 50,
    context_lines: int = 0,
    config_path: str = "",
    ha_url: str = "",
    ha_token: str = "",
) -> dict[str, Any]:
    max_results = int(max_results)
    context_lines = min(int(context_lines), 5)

    results = []

    log_files = []
    if log_source in ["current", "both"]:
        log_files.append(("current", "home-assistant.log"))
    if log_source in ["previous", "both"]:
        log_files.append(("previous", "home-assistant.log.1"))

    for source_name, log_file in log_files:
        log_lines, _ = _acquire_log_lines(log_file, config_path, ha_url, ha_token)

        if not log_lines:
            continue

        for i, line in enumerate(log_lines):
            if search_term.lower() in line.lower():
                result = {
                    "source": source_name,
                    "line_number": i + 1,
                    "content": line.strip()[:300],
                }

                if context_lines > 0:
                    start = max(0, i - context_lines)
                    end = min(len(log_lines), i + context_lines + 1)
                    result["context"] = "".join(log_lines[start:end])[:500]

                results.append(result)

                if len(results) >= max_results:
                    break

        if len(results) >= max_results:
            break

    if not results:
        return {
            "search_term": search_term,
            "total_found": 0,
            "message": f"No occurrences of '{search_term}' found",
        }

    return {
        "search_term": search_term,
        "total_found": len(results),
        "results": results,
    }


def _do_get_component_logs(
    component_name: str,
    log_source: str = "current",
    max_results: int = 100,
    config_path: str = "",
    ha_url: str = "",
    ha_token: str = "",
) -> dict[str, Any]:
    max_results = int(max_results)
    results = []

    log_files = []
    if log_source in ["current", "both"]:
        log_files.append(("current", "home-assistant.log"))
    if log_source in ["previous", "both"]:
        log_files.append(("previous", "home-assistant.log.1"))

    for source_name, log_file in log_files:
        log_lines, _ = _acquire_log_lines(log_file, config_path, ha_url, ha_token)

        if not log_lines:
            continue

        for line in log_lines:
            parsed = _parse_log_line(line)

            if parsed.get("unparsed"):
                continue

            component = parsed.get("component", "")

            if component_name.lower() in component.lower():
                results.append(
                    {
                        "source": source_name,
                        "timestamp": parsed.get("timestamp"),
                        "level": parsed.get("level"),
                        "component": component,
                        "message": parsed.get("message", "")[:300],
                    }
                )

                if len(results) >= max_results:
                    break

        if len(results) >= max_results:
            break

    if not results:
        return {
            "component": component_name,
            "total_found": 0,
            "message": f"No logs found for component '{component_name}'",
        }

    return {
        "component": component_name,
        "total_found": len(results),
        "logs": results,
    }


# ========================================
# TIMELINE & STARTUP
# ========================================


def _do_get_startup_errors(
    config_path: str = "",
    ha_url: str = "",
    ha_token: str = "",
) -> dict[str, Any]:
    log_lines, _ = _acquire_log_lines("home-assistant.log", config_path, ha_url, ha_token)

    if not log_lines:
        return {"error": "home-assistant.log not found and the Supervisor log proxy is unavailable"}

    startup_logs = []
    found_startup = False

    for i in range(len(log_lines) - 1, -1, -1):
        if "Starting Home Assistant" in log_lines[i]:
            found_startup = True
            startup_logs = log_lines[i:]
            break

    if not found_startup:
        return {"error": "Could not find startup marker in logs"}

    errors = []
    warnings = []

    for line in startup_logs:
        parsed = _parse_log_line(line)

        if parsed.get("unparsed"):
            continue

        level = parsed.get("level", "")

        if level == "ERROR":
            errors.append(
                {
                    "timestamp": parsed.get("timestamp"),
                    "component": parsed.get("component"),
                    "message": parsed.get("message", "")[:300],
                }
            )
        elif level == "WARNING":
            warnings.append(
                {
                    "timestamp": parsed.get("timestamp"),
                    "component": parsed.get("component"),
                    "message": parsed.get("message", "")[:300],
                }
            )

    return {
        "startup_errors": errors[-20:],
        "startup_warnings": warnings[-20:],
        "total_errors": len(errors),
        "total_warnings": len(warnings),
    }


def _do_get_log_timeline(
    hours: str = "1",
    log_source: str = "current",
    config_path: str = "",
    ha_url: str = "",
    ha_token: str = "",
) -> dict[str, Any]:
    try:
        if isinstance(hours, str):
            extracted = re.search(r"(\d+)", hours)
            val = int(extracted.group(1)) if extracted else 1
        else:
            val = int(hours)
        hours_int = min(max(val, 1), 24)
    except (ValueError, AttributeError, TypeError):
        hours_int = 1

    log_file = "home-assistant.log" if log_source == "current" else "home-assistant.log.1"
    log_lines, source_meta = _acquire_log_lines(log_file, config_path, ha_url, ha_token)

    if not log_lines:
        return {
            "error": (
                f"{log_file} not found and the Supervisor log proxy "
                "(api/hassio/core/logs) is unavailable"
            )
        }

    # Source timestamps are naive wall-clock values. Do not relabel them UTC.
    cutoff_time = datetime.now(UTC).astimezone().replace(tzinfo=None) - timedelta(hours=hours_int)
    timeline = []

    for line in log_lines:
        parsed = _parse_log_line(line)

        if parsed.get("unparsed"):
            continue

        timestamp_str = parsed.get("timestamp", "")
        level = parsed.get("level", "")

        if level not in ["ERROR", "WARNING"]:
            continue

        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

            if timestamp >= cutoff_time:
                timeline.append(
                    {
                        "timestamp": timestamp_str,
                        "level": level,
                        "component": parsed.get("component"),
                        "message": parsed.get("message", "")[:200],
                    }
                )
        except (ValueError, TypeError):
            continue

    total_found = len(timeline)

    return {
        "log_source": log_file,
        "time_range_hours": hours_int,
        "raw_level_counts": {
            "ERROR": sum(1 for event in timeline if event["level"] == "ERROR"),
            "WARNING": sum(1 for event in timeline if event["level"] == "WARNING"),
        },
        "cutoff_reason": "TRUNCATED_MAX_LINES" if source_meta.get("truncated") else "NONE",
        "time_basis": "SOURCE_LOG_LOCAL_UNVERIFIED",
        "requested_utc_window": {
            "status": "UNVERIFIED",
            "reason": "Source timestamps have no trusted UTC offset",
        },
        "observed_source_window": {
            "earliest": min((event["timestamp"] for event in timeline), default=None),
            "latest": max((event["timestamp"] for event in timeline), default=None),
        },
        "count_semantics": {
            "version": 1,
            "raw": "SEVERITY_PREFIX",
            "grouped": "NORMALIZED_SIGNATURE",
        },
        "total_events_found": total_found,
        "events_returned": min(len(timeline), 100),
        "timeline": timeline[-100:],
    }


# ========================================
# TOOL REGISTRATION
# ========================================


def register_log_tools(  # type: ignore[no-untyped-def]
    mcp, config_path: str, ha_url: str = "", ha_token: str = ""
) -> None:
    """
    Registers tools for analyzing Home Assistant logs.
    """

    @mcp.tool()
    def get_log_insights(
        hours: int = 1,
        severity: str = "warning",
        group_similar: bool = True,
        include_affected_entities: bool = True,
        max_patterns: int = 10,
    ) -> str:
        """[READ] Intelligent log analysis: grouped error patterns, affected entities, problem categories, timeline, and fix recommendations. ~85% token savings.

        Returns:
        - Grouped errors with affected entities and affected automations
        - Problem categorization
        - Timeline summary
        - Fix recommendations

        Args:
            hours: Number of hours back (1-24, default: 1)
            severity: "error", "warning", "all" (default: "warning")
            group_similar: Whether to group recurring errors (default: True)
            include_affected_entities: Whether to include entity list (default: True)
            max_patterns: Maximum number of patterns to return (default: 10)

        Returns:
            JSON with processed insights
        """
        try:
            result = _do_get_log_insights(
                hours=hours,
                severity=severity,
                group_similar=group_similar,
                include_affected_entities=include_affected_entities,
                max_patterns=max_patterns,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if isinstance(result, dict) and "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    def analyze_log_errors(
        log_source: str = "current",
        max_results: int = 50,
        since_source_time: str | None = None,
        until_source_time: str | None = None,
        source_timezone: str | None = None,
        owner_component: str | None = None,
    ) -> str:
        """[READ] Bounded error and warning summary with source coverage.

        Source-local log times have no verified UTC offset unless the caller
        supplies a trusted timezone or fixed offset. Counts are observations,
        never proof that a boot or deployment had zero errors.

        Args:
            log_source: "current" or "previous"
            max_results: Requested maximum patterns (positive integer, capped at 20).
            since_source_time: Inclusive YYYY-MM-DD HH:MM:SS in source log time
            until_source_time: Inclusive YYYY-MM-DD HH:MM:SS in source log time
            source_timezone: Trusted IANA zone (e.g. Europe/Warsaw) or +HH:MM offset
            owner_component: Optional component/owner scope for a post-deploy receipt

        Returns:
            JSON with raw severity counts, grouped signatures, bounded
            provenance, boot/rotation identity, UTC evidence when trusted,
            optional owner receipt and explicit coverage status.
        """
        try:
            result = _do_analyze_log_errors(
                log_source=log_source,
                max_results=max_results,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
                since_source_time=since_source_time,
                until_source_time=until_source_time,
                source_timezone=source_timezone,
                owner_component=owner_component,
            )
            if isinstance(result, dict) and "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    def get_recent_logs(lines: int = 100, level: str = "all") -> str:
        """[READ] Fetches last N lines from current log.

        Args:
            lines: Number of last lines (default: 100, max 500)
            level: Level filter - "all", "error", "warning", "info", "debug"

        Returns:
            JSON with requested and returned line counts, level filter, and
            the raw log content string.
        """
        try:
            result = _do_get_recent_logs(
                lines=lines,
                level=level,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if isinstance(result, dict) and "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(f"Error reading logs: {str(e)}")

    @mcp.tool()
    def get_previous_logs(lines: int = 100, level: str = "all") -> str:
        """[READ] Fetches last N lines from previous log.

        Args:
            lines: Number of last lines (default: 100, max 500)
            level: Level filter - "all", "error", "warning", "info", "debug"

        Returns:
            JSON with requested and returned line counts, level filter, and
            the raw log content string.
        """
        try:
            result = _do_get_previous_logs(
                lines=lines,
                level=level,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if isinstance(result, dict) and "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(f"Error reading logs: {str(e)}")

    @mcp.tool()
    def search_logs(
        search_term: str,
        log_source: str = "current",
        max_results: int = 50,
        context_lines: int = 0,
    ) -> str:
        """[READ] Searches for a specific phrase in logs with optional context.

        Args:
            search_term: Phrase to search for
            log_source: "current", "previous", or "both"
            max_results: Maximum number of results
            context_lines: Number of context lines before and after (0-5)

        Returns:
            JSON with search term, total matches, and per-match source, line
            number, content, and optional surrounding context.
        """
        try:
            result = _do_search_logs(
                search_term=search_term,
                log_source=log_source,
                max_results=max_results,
                context_lines=context_lines,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if isinstance(result, dict) and "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    def get_component_logs(
        component_name: str, log_source: str = "current", max_results: int = 100
    ) -> str:
        """[READ] Fetches all logs related to a specific component/integration.

        Args:
            component_name: Component name (e.g. "homeassistant.core")
            log_source: "current", "previous", or "both"
            max_results: Maximum number of results

        Returns:
            JSON with component name, total matches, and log entries with
            source, timestamp, level, and message.
        """
        try:
            result = _do_get_component_logs(
                component_name=component_name,
                log_source=log_source,
                max_results=max_results,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if isinstance(result, dict) and "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    def get_startup_errors() -> str:
        """Analyzes startup logs and returns errors/warnings from last startup.

        Returns:
            JSON with startup errors and warnings, and total counts for each.
        """
        try:
            result = _do_get_startup_errors(
                config_path=config_path, ha_url=ha_url, ha_token=ha_token
            )
            if isinstance(result, dict) and "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    def get_log_timeline(hours: str = "1", log_source: str = "current") -> str:
        """[READ] Creates timeline of errors and warnings from last N hours.

        Args:
            hours: Number of hours back (1-24, default: 1)
            log_source: "current" or "previous"

        Returns:
            JSON with log source, time range, total and returned event counts,
            and a timeline of error and warning entries.
        """
        try:
            result = _do_get_log_timeline(
                hours=hours,
                log_source=log_source,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if isinstance(result, dict) and "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

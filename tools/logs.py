"""
Log Management Tools
Provides tools for reading, analyzing, and searching Home Assistant logs.
Optimized for AI context windows with intelligent caching and filtering.

Optimizations:
- get_log_insights() now returns affected_entities and affected_automations per pattern
- Better error grouping
- TTL cache
"""

import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tools.utils import tail_log_file

# Simple in-memory cache for log analysis results
_LOG_CACHE: Dict[str, Tuple[Any, float]] = {}
_CACHE_TTL = 60  # seconds


def _get_cached_result(key: str) -> Optional[Any]:
    """Returns cached result if valid, None otherwise."""
    if key in _LOG_CACHE:
        data, timestamp = _LOG_CACHE[key]
        if time.time() - timestamp < _CACHE_TTL:
            return data
    return None


def _set_cache_result(key: str, data: Any) -> None:
    """Sets cache result with current timestamp."""
    _LOG_CACHE[key] = (data, time.time())


def register_log_tools(mcp, config_path: str):
    """
    Registers tools for analyzing Home Assistant logs.
    """

    # Pre-compiled regex patterns for performance
    TIMESTAMP_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)?")
    COMPONENT_PATTERN = re.compile(r"\[([^\]]+)\]")
    LEVEL_PATTERN = re.compile(r"\b(ERROR|WARNING|INFO|DEBUG)\b")

    ENTITY_PATTERN = re.compile(
        r"\b(sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|"
        r"person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|"
        r"timer|counter|number|select|button|scene|group|alarm_control_panel)\.[a-zA-Z0-9_]+\b"
    )

    AUTOMATION_PATTERN = re.compile(r"automation\.([a-zA-Z0-9_]+)")

    # ========================================
    # 🛠️ INTERNAL HELPERS
    # ========================================

    def _parse_log_line(line: str) -> dict:
        """Parse a single log line into structured data."""
        result = {"raw": line.strip()}

        # Extract timestamp
        ts_match = TIMESTAMP_PATTERN.search(line)
        if ts_match:
            result["timestamp"] = ts_match.group(1)

        # Extract level
        level_match = LEVEL_PATTERN.search(line)
        if level_match:
            result["level"] = level_match.group(1)

        # Extract component
        comp_match = COMPONENT_PATTERN.search(line)
        if comp_match:
            result["component"] = comp_match.group(1)

        # Extract message (after component)
        if comp_match:
            msg_start = comp_match.end()
            result["message"] = line[msg_start:].strip()
        else:
            result["message"] = line.strip()

        if "timestamp" not in result and "level" not in result:
            result["unparsed"] = True

        return result

    def _read_log_file(log_file: str, max_lines: int = None) -> Optional[List[str]]:
        """Read log file efficiently, handling encoding errors."""
        log_path = Path(config_path) / log_file
        if not log_path.exists():
            return None

        try:
            if max_lines:
                return [
                    line + "\n" if not line.endswith("\n") else line
                    for line in tail_log_file(str(log_path), lines=max_lines)
                ]

            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.readlines()
        except Exception:
            return None

    def _extract_entities(text: str) -> Set[str]:
        """Extract all entity ids from text."""
        return set(ENTITY_PATTERN.findall(text))

    def _extract_full_entity_ids(text: str) -> Set[str]:
        """Extract full entity ids (domain.name) from text."""
        pattern = r"\b(?:sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|timer|counter|number|select|button|scene|group|alarm_control_panel)\.[a-zA-Z0-9_]+\b"
        return set(re.findall(pattern, text))

    def _extract_automations(text: str) -> Set[str]:
        """Extract automation ids from text."""
        matches = AUTOMATION_PATTERN.findall(text)
        return {f"automation.{m}" for m in matches}

    def _normalize_pattern(message: str) -> str:
        """Normalize message for grouping similar errors."""
        normalized = message
        # Remove timestamps
        normalized = re.sub(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?", "TIMESTAMP", normalized
        )
        # Remove UUids and hex ids
        normalized = re.sub(r"[a-f0-9]{8,}", "ID", normalized)
        # Remove IP addresses
        normalized = re.sub(r"\d+\.\d+\.\d+\.\d+", "IP", normalized)
        # Remove numbers (but keep entity structure)
        normalized = re.sub(r"(?<![a-z_])\d+(?![a-z_])", "N", normalized)
        # Limit length
        return normalized[:150].strip()

    def _categorize_error(component: str, message: str) -> str:
        """Categorize error typeee based on message content."""
        msg_lower = message.lower()

        if "timeout" in msg_lower or "timed out" in msg_lower:
            return "timeout"
        elif "connection" in msg_lower or "connect" in msg_lower or "refused" in msg_lower:
            return "connection"
        elif "unavailable" in msg_lower or "not available" in msg_lower:
            return "unavailable"
        elif (
            "permission" in msg_lower or "access denied" in msg_lower or "unauthorized" in msg_lower
        ):
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
    # 🚀 OPTIMIZED LOG ANALYSIS
    # ========================================

    @mcp.tool()
    def get_log_insights(
        hours: int = 1,
        severity: str = "warning",
        group_similar: bool = True,
        include_affected_entities: bool = True,
        max_patterns: int = 10,
    ) -> str:
        """
        🚀 INTELLIGENT LOG ANALYSIS - Processed insights instead of raw logs.

        ~85% token savings! Returns:
        - Pogrupowane errors z affected_entities i affected_automations
        - Problem categorization
        - Timeline summary
        - Recommendations naprawy

        Args:
            hours: Number of hours back (1-24, default: 1)
            severity: "error", "warning", "all" (default: "warning")
            group_similar: Whether to group recurring errors (default: True)
            include_affected_entities: Whether to include entity list (default: True)
            max_patterns: Maximum number of patterns to return (default: 10)

        Returns:
            JSON with processed insights
        """
        cache_key = f"insights_{hours}_{severity}_{group_similar}_{include_affected_entities}_{max_patterns}"
        cached = _get_cached_result(cache_key)
        if cached:
            return cached

        try:
            hours = min(max(int(hours), 1), 24)
            log_lines = _read_log_file("home-assistant.log")

            if not log_lines:
                return json.dumps(
                    {"success": False, "error": "home-assistant.log not found"},
                    indent=2,
                )

            cutoff_time = datetime.now() - timedelta(hours=hours)

            # Data structures
            errors: List[Dict] = []
            warnings: List[Dict] = []
            all_entities: Set[str] = set()
            all_automations: Set[str] = set()
            component_counter: Counter = Counter()
            category_counter: Counter = Counter()

            # Pattern grouping with affected entities
            pattern_data: Dict[str, Dict] = defaultdict(
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
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
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
                sorted_patterns = sorted(
                    pattern_data.items(), key=lambda x: x[1]["count"], reverse=True
                )[:max_patterns]

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
                    "affected_entities_count": len(all_entities)
                    if include_affected_entities
                    else None,
                    "affected_automations_count": len(all_automations)
                    if include_affected_entities
                    else None,
                },
                "grouped_errors": grouped_errors if group_similar else {},
                "error_categories": dict(category_counter.most_common()),
                "affected_components": [
                    {"component": comp, "count": count}
                    for comp, count in component_counter.most_common(15)
                ],
                "affected_entities": sorted(list(all_entities))[:30]
                if include_affected_entities
                else [],
                "affected_automations": sorted(list(all_automations))[:10]
                if include_affected_entities
                else [],
                "recent_errors": errors[-5:],
                "recent_warnings": warnings[-5:],
                "recommendations": [],
            }

            # Generate recommendations
            if category_counter["timeout"] > 5:
                result["recommendations"].append(
                    {
                        "priority": "high",
                        "category": "timeout",
                        "message": f"High timeout count ({category_counter['timeout']}). Check network connectivity and device responsiveness.",
                    }
                )

            if category_counter["unavailable"] > 10:
                result["recommendations"].append(
                    {
                        "priority": "high",
                        "category": "unavailable",
                        "message": f"Many unavailable entities ({category_counter['unavailable']}). Check integrations.",
                    }
                )

            if category_counter["template"] > 3:
                result["recommendations"].append(
                    {
                        "priority": "medium",
                        "category": "template",
                        "message": f"Template errors detected ({category_counter['template']}). Use Developer Tools to debug.",
                    }
                )

            if category_counter["api"] > 5:
                result["recommendations"].append(
                    {
                        "priority": "medium",
                        "category": "api",
                        "message": f"API errors detected ({category_counter['api']}). Check external service status and rate limits.",
                    }
                )

            top_component = component_counter.most_common(1)
            if top_component and top_component[0][1] > 10:
                result["recommendations"].append(
                    {
                        "priority": "high",
                        "category": "component",
                        "message": f"Component '{top_component[0][0]}' generates most errors ({top_component[0][1]}). Investigate this integration.",
                    }
                )

            # Check for specific automation issues
            if all_automations:
                result["recommendations"].append(
                    {
                        "priority": "medium",
                        "category": "automation",
                        "message": f"Automations with issues: {', '.join(list(all_automations)[:3])}",
                    }
                )

            if not result["recommendations"]:
                result["recommendations"].append(
                    {
                        "priority": "info",
                        "category": "general",
                        "message": "No critical issues detected in this time period.",
                    }
                )

            json_result = json.dumps(result, indent=2, ensure_ascii=False)
            _set_cache_result(cache_key, json_result)
            return json_result

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def analyze_log_errors(log_source: str = "current", max_results: int = 50) -> str:
        """
        Analyzes logs and returns intelligent summary of errors and warnings.

        Args:
            log_source: "current" or "previous"
            max_results: Maximum number of results (default: 50)
        """
        cache_key = f"analyze_{log_source}_{max_results}"
        cached = _get_cached_result(cache_key)
        if cached:
            return cached

        try:
            max_results = int(max_results)

            log_file = "home-assistant.log" if log_source == "current" else "home-assistant.log.1"
            log_lines = _read_log_file(log_file)

            if not log_lines:
                return json.dumps({"success": False, "error": f"{log_file} not found"}, indent=2)

            errors = []
            warnings = []
            tracebacks = []
            device_issues = defaultdict(list)
            permission_errors = []

            current_traceback = []
            in_traceback = False

            for line in log_lines:
                parsed = _parse_log_line(line)

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

            summary = {
                "success": True,
                "log_source": log_file,
                "total_errors": len(errors),
                "total_warnings": len(warnings),
                "total_tracebacks": len(tracebacks),
                "most_common_errors": [
                    {"message": msg, "count": count}
                    for msg, count in error_counter.most_common(max_results)
                ][:20],
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

            json_result = json.dumps(summary, indent=2, ensure_ascii=False)
            _set_cache_result(cache_key, json_result)
            return json_result

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    # ========================================
    # 📋 BASIC LOG READING
    # ========================================

    @mcp.tool()
    def get_recent_logs(lines: int = 100, level: str = "all") -> str:
        """
        Fetches last N lines from current log.

        Args:
            lines: Number of last lines (default: 100, max 500)
            level: Level filter - "all", "error", "warning", "info", "debug"
        """
        try:
            lines = min(int(lines), 500)
            log_lines = _read_log_file("home-assistant.log", lines * 2)

            if not log_lines:
                return json.dumps(
                    {"success": False, "error": "home-assistant.log not found"},
                    indent=2,
                )

            if level.lower() != "all":
                level_upper = level.upper()
                log_lines = [line for line in log_lines if level_upper in line]

            result_lines = log_lines[-lines:]
            return json.dumps(
                {
                    "success": True,
                    "lines_requested": lines,
                    "lines_returned": len(result_lines),
                    "level_filter": level,
                    "logs": "".join(result_lines),
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {"success": False, "error": f"Error reading logs: {str(e)}"},
                indent=2,
            )

    @mcp.tool()
    def get_previous_logs(lines: int = 100, level: str = "all") -> str:
        """
        Fetches last N lines from previous log.

        Args:
            lines: Number of last lines (default: 100, max 500)
            level: Level filter - "all", "error", "warning", "info", "debug"
        """
        try:
            lines = min(int(lines), 500)
            log_lines = _read_log_file("home-assistant.log.1", lines * 2)

            if not log_lines:
                return json.dumps(
                    {"success": False, "error": "home-assistant.log.1 not found"},
                    indent=2,
                )

            if level.lower() != "all":
                level_upper = level.upper()
                log_lines = [line for line in log_lines if level_upper in line]

            result_lines = log_lines[-lines:]
            return json.dumps(
                {
                    "success": True,
                    "lines_requested": lines,
                    "lines_returned": len(result_lines),
                    "level_filter": level,
                    "logs": "".join(result_lines),
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {"success": False, "error": f"Error reading logs: {str(e)}"},
                indent=2,
            )

    # ========================================
    # 🔍 LOG SEARCH
    # ========================================

    @mcp.tool()
    def search_logs(
        search_term: str,
        log_source: str = "current",
        max_results: int = 50,
        context_lines: int = 0,
    ) -> str:
        """
        Searches for a specific phrase in logs with optional context.

        Args:
            search_term: Phrase to search for
            log_source: "current", "previous", or "both"
            max_results: Maximum number of results
            context_lines: Number of context lines before and after (0-5)
        """
        try:
            max_results = int(max_results)
            context_lines = min(int(context_lines), 5)

            results = []

            log_files = []
            if log_source in ["current", "both"]:
                log_files.append(("current", "home-assistant.log"))
            if log_source in ["previous", "both"]:
                log_files.append(("previous", "home-assistant.log.1"))

            for source_name, log_file in log_files:
                log_lines = _read_log_file(log_file)

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
                return json.dumps(
                    {
                        "success": True,
                        "search_term": search_term,
                        "total_found": 0,
                        "message": f"No occurrences of '{search_term}' found",
                    },
                    indent=2,
                )

            return json.dumps(
                {
                    "success": True,
                    "search_term": search_term,
                    "total_found": len(results),
                    "results": results,
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def get_component_logs(
        component_name: str, log_source: str = "current", max_results: int = 100
    ) -> str:
        """
        Fetches all logs related to a specific component/integration.

        Args:
            component_name: Component name (e.g. "homeassistant.core")
            log_source: "current", "previous", or "both"
            max_results: Maximum number of results
        """
        try:
            max_results = int(max_results)
            results = []

            log_files = []
            if log_source in ["current", "both"]:
                log_files.append(("current", "home-assistant.log"))
            if log_source in ["previous", "both"]:
                log_files.append(("previous", "home-assistant.log.1"))

            for source_name, log_file in log_files:
                log_lines = _read_log_file(log_file)

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
                return json.dumps(
                    {
                        "success": True,
                        "component": component_name,
                        "total_found": 0,
                        "message": f"No logs found for component '{component_name}'",
                    },
                    indent=2,
                )

            return json.dumps(
                {
                    "success": True,
                    "component": component_name,
                    "total_found": len(results),
                    "logs": results,
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    # ========================================
    # 🕐 TIMELINE & STARTUP
    # ========================================

    @mcp.tool()
    def get_startup_errors() -> str:
        """
        Analyzes startup logs and returns errors/warnings from last startup.
        """
        try:
            log_lines = _read_log_file("home-assistant.log")

            if not log_lines:
                return json.dumps(
                    {"success": False, "error": "home-assistant.log not found"},
                    indent=2,
                )

            startup_logs = []
            found_startup = False

            for i in range(len(log_lines) - 1, -1, -1):
                if "Starting Home Assistant" in log_lines[i]:
                    found_startup = True
                    startup_logs = log_lines[i:]
                    break

            if not found_startup:
                return json.dumps(
                    {
                        "success": False,
                        "error": "Could not find startup marker in logs",
                    },
                    indent=2,
                )

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

            return json.dumps(
                {
                    "success": True,
                    "startup_errors": errors[-20:],  # Limit
                    "startup_warnings": warnings[-20:],
                    "total_errors": len(errors),
                    "total_warnings": len(warnings),
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def get_log_timeline(hours: str = "1", log_source: str = "current") -> str:
        """
        Creates timeline of errors and warnings from last N hours.

        Args:
            hours: Number of hours back (1-24, default: 1)
            log_source: "current" or "previous"
        """
        try:
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
            log_lines = _read_log_file(log_file)

            if not log_lines:
                return json.dumps({"success": False, "error": f"{log_file} not found"}, indent=2)

            cutoff_time = datetime.now() - timedelta(hours=hours_int)
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

            return json.dumps(
                {
                    "success": True,
                    "log_source": log_file,
                    "time_range_hours": hours_int,
                    "total_events_found": total_found,
                    "events_returned": min(len(timeline), 100),
                    "timeline": timeline[-100:],
                },
                indent=2,
                ensure_ascii=False,
            )

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

"""
System Diagnostics Tools
High-level tools for analyzing Home Assistant health, integration status, and energy usage.
Aggregates data from multiple sources (API, Registries, Logs) to save AI tokens.

Optimizations:
- diagnose_system_health() now contains everything in a single call (~90% token savings)
- get_unavailable_entities_grouped() groups entities instead of listing (~95% savings)
- TTL cache for recurring queries
"""

import logging
import math
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from tools.utils import _error_response, _success_response, load_registry, make_ha_request

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"

# ========================================
# CACHE CONFIGURATION
# ========================================

_DIAGNOSTICS_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_TTL = 60  # seconds
_CACHE_LOCK = threading.Lock()


def _get_cached(key: str) -> Any | None:
    """Returns data from cache if it is current."""
    with _CACHE_LOCK:
        if key in _DIAGNOSTICS_CACHE:
            data, timestamp = _DIAGNOSTICS_CACHE[key]
            if time.time() - timestamp < _CACHE_TTL:
                return data
    return None


def _set_cache(key: str, data: Any) -> None:
    """Writes data to cache with timestamp."""
    with _CACHE_LOCK:
        _DIAGNOSTICS_CACHE[key] = (data, time.time())


def _clear_cache() -> None:
    """Clears entire cache (used in tests)."""
    with _CACHE_LOCK:
        _DIAGNOSTICS_CACHE.clear()


# ========================================
# INTERNAL HELPERS
# ========================================


def _get_log_errors_with_patterns(
    config_path: str,
    hours: int = 1,
    max_patterns: int = 10,
) -> dict[str, Any]:
    """
    Counts errors in logs from last N hours and groups them into patterns.
    Also returns affected entities for each pattern.
    """
    log_path = Path(config_path) / "home-assistant.log"
    if not log_path.exists():
        return {
            "errors": 0,
            "warnings": 0,
            "top_error_patterns": [],
            "api_errors": [],
            "slow_entities": [],
        }

    errors = 0
    warnings = 0
    error_patterns: dict[str, dict] = defaultdict(  # type: ignore[type-arg]
        lambda: {
            "count": 0,
            "component": "",
            "severity": "error",
            "affected_entities": set(),
            "first_seen": None,
            "last_seen": None,
            "sample_message": "",
        }
    )
    api_errors: list[dict] = []  # type: ignore[type-arg]
    slow_entities: list[dict] = []  # type: ignore[type-arg]

    cutoff = datetime.now() - timedelta(hours=hours)

    # Regex patterns
    entity_pattern = re.compile(
        r"\b(sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|"
        r"person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|"
        r"timer|counter|number|select|button|scene|group|alarm_control_panel)\.[a-zA-Z0-9_]+\b"
    )
    timestamp_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    component_pattern = re.compile(r"\[([^\]]+)\]")
    slow_pattern = re.compile(r"took (\d+\.?\d*)\s*(?:seconds|s)|(\d+\.?\d*)\s*(?:seconds|s) to")
    api_error_pattern = re.compile(
        r"(4\d{2}|5\d{2})\s*(Rate Limit|Unauthorized|Forbidden|Not Found|Server Error|timeout)",
        re.IGNORECASE,
    )

    try:
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-10000:]

            for line in lines:
                is_error = "ERROR" in line
                is_warning = "WARNING" in line

                if not is_error and not is_warning:
                    continue

                # Date parsing
                try:
                    ts_match = timestamp_pattern.match(line)
                    if ts_match:
                        ts_str = ts_match.group(1)
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        if ts < cutoff:
                            continue
                    else:
                        continue
                except (ValueError, AttributeError):
                    continue

                if is_error:
                    errors += 1
                elif is_warning:
                    warnings += 1

                # Extract component
                comp_match = component_pattern.search(line)
                component = comp_match.group(1) if comp_match else "unknown"

                # Extract affected entities
                set(entity_pattern.findall(line))
                full_entities = set(
                    re.findall(
                        r"\b(?:sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|"
                        r"person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|"
                        r"timer|counter|number|select|button|scene|group|alarm_control_panel)\.[a-zA-Z0-9_]+\b",
                        line,
                    )
                )

                # Create pattern key (normalize dynamic parts)
                pattern_key = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+", "TIMESTAMP", line)
                pattern_key = re.sub(r"[a-f0-9]{8,}", "ID", pattern_key)
                pattern_key = re.sub(r"\d+\.\d+\.\d+\.\d+", "IP", pattern_key)
                pattern_key = re.sub(r"\d+", "N", pattern_key)
                pattern_key = pattern_key[:150].strip()

                # Update pattern stats
                pattern_data = error_patterns[pattern_key]
                pattern_data["count"] += 1
                pattern_data["component"] = component
                pattern_data["severity"] = "error" if is_error else "warning"
                pattern_data["affected_entities"].update(full_entities)
                if not pattern_data["first_seen"]:
                    pattern_data["first_seen"] = ts_str
                pattern_data["last_seen"] = ts_str
                if not pattern_data["sample_message"]:
                    msg_start = line.find("]") + 1 if "]" in line else 0
                    pattern_data["sample_message"] = line[msg_start : msg_start + 200].strip()

                # Detect API errors
                api_match = api_error_pattern.search(line)
                if api_match:
                    http_code = api_match.group(1)
                    error_type = api_match.group(2).lower().replace(" ", "_")
                    existing = next(
                        (
                            e
                            for e in api_errors
                            if e["component"] == component and e["error_type"] == error_type
                        ),
                        None,
                    )
                    if existing:
                        existing["count"] += 1
                        existing["last_occurrence"] = ts_str
                    else:
                        api_errors.append(
                            {
                                "integration": component.split(".")[-1]
                                if "." in component
                                else component,
                                "component": component,
                                "error_type": error_type,
                                "http_code": int(http_code) if http_code.isdigit() else None,
                                "count": 1,
                                "last_occurrence": ts_str,
                            }
                        )

                # Detect slow entities
                slow_match = slow_pattern.search(line)
                if slow_match:
                    try:
                        duration = float(slow_match.group(1) or slow_match.group(2))
                        if duration > 0.5:
                            for entity in full_entities:
                                existing_slow = next(
                                    (s for s in slow_entities if s["entity_id"] == entity),
                                    None,
                                )
                                if existing_slow:
                                    existing_slow["occurrences"] += 1
                                    existing_slow["max_time"] = max(
                                        existing_slow["max_time"], duration
                                    )
                                else:
                                    slow_entities.append(
                                        {
                                            "entity_id": entity,
                                            "update_time_seconds": duration,
                                            "max_time": duration,
                                            "occurrences": 1,
                                            "component": component,
                                            "threshold": 0.5,
                                        }
                                    )
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass

    sorted_patterns = sorted(error_patterns.items(), key=lambda x: x[1]["count"], reverse=True)[
        :max_patterns
    ]

    top_patterns = []
    for pattern_key, data in sorted_patterns:
        top_patterns.append(
            {
                "pattern": data["sample_message"][:100]
                if data["sample_message"]
                else pattern_key[:100],
                "count": data["count"],
                "component": data["component"],
                "severity": data["severity"],
                "affected_entities": list(data["affected_entities"])[:10],
                "first_seen": data["first_seen"],
                "last_seen": data["last_seen"],
            }
        )

    slow_entities.sort(key=lambda x: x["max_time"], reverse=True)

    return {
        "errors": errors,
        "warnings": warnings,
        "top_error_patterns": top_patterns,
        "api_errors": api_errors[:10],
        "slow_entities": slow_entities[:10],
    }


def _get_unavailable_by_integration(
    states: list[dict[str, Any]],
    entity_registry: list[dict[str, Any]],
    device_registry: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Groups unavailable entities by integration.
    Returns summary instead of full list of entities.
    """
    unavailable_states = [s for s in states if s["state"] in ["unavailable", "unknown"]]

    def is_ignorable(entity_id: str) -> bool:
        ignorable_domains = ["sun", "weather", "calendar", "update"]
        ignorable_patterns = [
            r"sensor\.sun_",
            r"sensor\..*_next_",
            r"binary_sensor\.workday",
        ]
        domain = entity_id.split(".")[0]
        if domain in ignorable_domains:
            return True
        for pattern in ignorable_patterns:
            if re.match(pattern, entity_id):
                return True
        return False

    unavailable_states = [s for s in unavailable_states if not is_ignorable(s["entity_id"])]

    entity_to_platform = {}
    entity_to_device = {}
    for e in entity_registry:
        entity_to_platform[e.get("entity_id", "")] = e.get("platform", "unknown")
        entity_to_device[e.get("entity_id", "")] = e.get("device_id")

    device_to_name = {}
    for d in device_registry:
        device_id = d.get("id", "")
        name = d.get("name") or d.get("name_by_user") or "Unknown Device"
        device_to_name[device_id] = name

    by_integration: dict[str, dict] = defaultdict(  # type: ignore[type-arg]
        lambda: {
            "count": 0,
            "devices": set(),
            "device_names": [],
            "sample_entities": [],
        }
    )

    for state in unavailable_states:
        entity_id = state["entity_id"]
        platform = entity_to_platform.get(entity_id, entity_id.split(".")[0])
        device_id = entity_to_device.get(entity_id)

        by_integration[platform]["count"] += 1

        if device_id:
            by_integration[platform]["devices"].add(device_id)
            device_name = device_to_name.get(device_id, "Unknown")
            if device_name not in by_integration[platform]["device_names"]:
                by_integration[platform]["device_names"].append(device_name)

        if len(by_integration[platform]["sample_entities"]) < 5:
            by_integration[platform]["sample_entities"].append(entity_id)

    result = {}
    for platform, data in sorted(by_integration.items(), key=lambda x: x[1]["count"], reverse=True):
        result[platform] = {
            "count": data["count"],
            "percentage": round(data["count"] / len(unavailable_states) * 100, 1)
            if unavailable_states
            else 0,
            "unique_devices": len(data["devices"]),
            "device_names": data["device_names"][:5],
            "sample_entities": data["sample_entities"],
        }

    return {"total_unavailable": len(unavailable_states), "by_integration": result}


def _get_by_domain(states: list[dict]) -> dict[str, int]:  # type: ignore[type-arg]
    """Groups unavailable entities by domain."""
    unavailable = [s for s in states if s["state"] in ["unavailable", "unknown"]]
    domain_counts = Counter()  # type: ignore[var-annotated]
    for s in unavailable:
        domain = s["entity_id"].split(".")[0]
        domain_counts[domain] += 1
    return dict(domain_counts.most_common())


# ========================================
# BUSINESS LOGIC FUNCTIONS
# ========================================


def _do_diagnose_system_health(
    include_log_analysis: bool = True,
    include_unavailable_breakdown: bool = True,
    include_performance: bool = True,
    hours_back: int = 1,
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    cache_key = f"diagnose_full_{include_log_analysis}_{include_unavailable_breakdown}_{include_performance}_{hours_back}"
    cached = _get_cached(cache_key)
    if cached:
        return cached  # type: ignore[no-any-return]

    hours_back = min(max(int(hours_back), 1), 24)

    states_res = make_ha_request(ha_url, ha_token, "/api/states")  # type: ignore[arg-type]
    if not states_res["success"]:
        return {"success": False, "error": "Cannot fetch system states"}

    states = states_res["data"]

    entity_reg = (
        load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])  # type: ignore[arg-type]
    )
    device_reg = (
        load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])  # type: ignore[arg-type]
    )

    total_entities = len(states)
    unavailable_raw = [s for s in states if s["state"] in ["unavailable", "unknown"]]

    def is_ignorable(entity_id: str) -> bool:
        ignorable_domains = ["sun", "weather", "calendar", "update"]
        domain = entity_id.split(".")[0]
        return domain in ignorable_domains

    unavailable = [s for s in unavailable_raw if not is_ignorable(s["entity_id"])]

    log_analysis = {
        "errors": 0,
        "warnings": 0,
        "top_error_patterns": [],
        "api_errors": [],
        "slow_entities": [],
    }
    if include_log_analysis or include_performance:
        log_analysis = _get_log_errors_with_patterns(config_path=config_path, hours=hours_back)  # type: ignore[arg-type]

    unavailable_breakdown = {}
    if include_unavailable_breakdown:
        unavailable_data = _get_unavailable_by_integration(states, entity_reg, device_reg)
        unavailable_breakdown = unavailable_data["by_integration"]

    notifications = [s for s in states if s["entity_id"].startswith("persistent_notification.")]

    score = 100
    score -= min(len(unavailable) * 2, 40)
    score -= min(log_analysis["errors"] * 3, 30)  # type: ignore[operator]
    score -= min(len(notifications) * 10, 20)
    score -= min(len(log_analysis.get("api_errors", [])) * 5, 10)  # type: ignore[arg-type]
    score = max(0, score)

    if score >= 80:
        status = "Healthy"
    elif score >= 50:
        status = "Warning"
    else:
        status = "Critical"

    recommendations = []

    if score < 50:
        recommendations.append(
            {
                "priority": "critical",
                "message": "System health is CRITICAL. Immediate attention required.",
            }
        )

    if unavailable_breakdown:
        top_integration = max(  # type: ignore[var-annotated]
            unavailable_breakdown.items(),
            key=lambda x: x[1]["count"],
            default=(None, {}),
        )
        if top_integration[0] and top_integration[1].get("count", 0) > 5:
            recommendations.append(
                {
                    "priority": "high",
                    "message": f"Integration '{top_integration[0]}' has {top_integration[1]['count']} unavailable entities. Check connection.",
                }
            )

    if log_analysis.get("api_errors"):
        for api_err in log_analysis["api_errors"][:2]:  # type: ignore[index]
            if api_err.get("error_type") == "rate_limit":
                recommendations.append(
                    {
                        "priority": "high",
                        "message": f"Rate limiting on {api_err.get('integration', 'unknown')}. Consider reducing polling frequency.",
                    }
                )
            elif api_err.get("http_code", 0) >= 500:
                recommendations.append(
                    {
                        "priority": "medium",
                        "message": f"Server errors from {api_err.get('integration', 'unknown')}. External service may be down.",
                    }
                )

    if log_analysis.get("top_error_patterns"):
        top_pattern = log_analysis["top_error_patterns"][0]  # type: ignore[index]
        if top_pattern["count"] > 10:
            recommendations.append(
                {
                    "priority": "high",
                    "message": f"Recurring error ({top_pattern['count']}x): {top_pattern['pattern'][:80]}...",
                }
            )

    if log_analysis.get("slow_entities"):
        slow = log_analysis["slow_entities"][0]  # type: ignore[index]
        if slow.get("max_time", 0) > 2.0:
            recommendations.append(
                {
                    "priority": "medium",
                    "message": f"Slow entity {slow['entity_id']}: {slow['max_time']:.1f}s response time.",
                }
            )

    if log_analysis["errors"] > 20:  # type: ignore[operator]
        recommendations.append(
            {
                "priority": "medium",
                "message": f"{log_analysis['errors']} errors in last {hours_back}h. Review log patterns above.",
            }
        )

    if notifications:
        recommendations.append(
            {
                "priority": "medium",
                "message": f"{len(notifications)} persistent notification(s). Check HA UI for details.",
            }
        )

    if score >= 80 and not recommendations:
        recommendations.append(
            {
                "priority": "info",
                "message": "System is healthy! No critical issues detected.",
            }
        )

    result = {
        "success": True,
        "summary": {
            "health_score": score,
            "status": status,
            "total_entities": total_entities,
            "unavailable_count": len(unavailable),
            "errors_last_hours": log_analysis["errors"],
            "warnings_last_hours": log_analysis["warnings"],
            "hours_analyzed": hours_back,
        },
        "unavailable_by_integration": unavailable_breakdown
        if include_unavailable_breakdown
        else None,
        "unavailable_by_domain": _get_by_domain(states) if len(unavailable) <= 50 else None,
        "top_error_patterns": log_analysis.get("top_error_patterns", [])
        if include_log_analysis
        else None,
        "api_errors": log_analysis.get("api_errors", []) if include_log_analysis else None,
        "slow_entities": log_analysis.get("slow_entities", []) if include_performance else None,
        "active_notifications": [
            {
                "title": n["attributes"].get("title", "Unknown"),
                "entity_id": n["entity_id"],
            }
            for n in notifications
        ],
        "recommendations": recommendations,
    }

    result = {k: v for k, v in result.items() if v is not None}

    return result


def _do_get_unavailable_entities_grouped(
    group_by: str = "integration",
    include_device_names: bool = True,
    max_sample_entities: int = 5,
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    cache_key = f"unavailable_grouped_{group_by}_{include_device_names}_{max_sample_entities}"
    cached = _get_cached(cache_key)
    if cached:
        return cached  # type: ignore[no-any-return]

    states_res = make_ha_request(ha_url, ha_token, "/api/states")  # type: ignore[arg-type]
    if not states_res["success"]:
        return {"success": False, "error": "Cannot fetch states"}

    states = states_res["data"]

    entity_reg = (
        load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])  # type: ignore[arg-type]
    )
    device_reg = (
        load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])  # type: ignore[arg-type]
    )

    if group_by == "integration":
        grouped_data = _get_unavailable_by_integration(states, entity_reg, device_reg)
        for platform, data in grouped_data["by_integration"].items():
            data["sample_entities"] = data["sample_entities"][:max_sample_entities]
            if not include_device_names:
                data.pop("device_names", None)
        result = {
            "success": True,
            "total_unavailable": grouped_data["total_unavailable"],
            "by_integration": grouped_data["by_integration"],
            "by_domain": _get_by_domain(states),
        }
    else:
        unavailable = [s for s in states if s["state"] in ["unavailable", "unknown"]]
        by_domain: dict[str, dict] = defaultdict(lambda: {"count": 0, "sample_entities": []})  # type: ignore[type-arg]
        for s in unavailable:
            domain = s["entity_id"].split(".")[0]
            by_domain[domain]["count"] += 1
            if len(by_domain[domain]["sample_entities"]) < max_sample_entities:
                by_domain[domain]["sample_entities"].append(s["entity_id"])
        result = {
            "success": True,
            "total_unavailable": len(unavailable),
            "by_domain": dict(by_domain),
        }

    return result


def _do_get_integration_health(
    domain: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    states_res = make_ha_request(ha_url, ha_token, "/api/states")  # type: ignore[arg-type]
    if not states_res["success"]:
        return {"success": False, "error": "API error"}

    entity_reg_data = load_registry("core.entity_registry", config_path)  # type: ignore[arg-type]
    entity_reg = entity_reg_data.get("data", {}).get("entities", [])

    platform_entity_ids = {e["entity_id"] for e in entity_reg if e.get("platform") == domain}

    domain_entities = [
        s
        for s in states_res["data"]
        if s["entity_id"].startswith(f"{domain}.") or s["entity_id"] in platform_entity_ids
    ]

    if not domain_entities:
        return {
            "success": False,
            "domain": domain,
            "status": "Not Found / No Entities",
            "error": f"No entities found for domain '{domain}'",
        }

    unavailable = [e for e in domain_entities if e["state"] in ["unavailable", "unknown"]]

    log_issues = []
    log_path = Path(config_path) / "home-assistant.log"  # type: ignore[arg-type]
    if log_path.exists():
        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                for line in f.readlines()[-2000:]:
                    if domain.lower() in line.lower() and ("ERROR" in line or "WARNING" in line):
                        log_issues.append(line.strip()[:200])
        except Exception:
            pass

    availability_pct = (
        ((len(domain_entities) - len(unavailable)) / len(domain_entities) * 100)
        if domain_entities
        else 0
    )

    if len(unavailable) == len(domain_entities) and len(domain_entities) > 0:
        status = "Critical (All Unavailable)"
    elif availability_pct < 50:
        status = "Warning (Majority Unavailable)"
    elif len(unavailable) > 0:
        status = "Warning (Some Unavailable)"
    else:
        status = "Healthy"

    recommendations = []
    if status.startswith("Critical"):
        recommendations.append(
            "Integration is completely down. Check connection and restart integration."
        )
    if len(log_issues) > 10:
        recommendations.append(
            f"High error rate in logs ({len(log_issues)} issues). Check integration configuration."
        )
    if len(unavailable) > 0 and not status.startswith("Critical"):
        recommendations.append(
            f"{len(unavailable)} entities unavailable. Check device connectivity."
        )

    return {
        "success": True,
        "domain": domain,
        "status": status,
        "stats": {
            "total_entities": len(domain_entities),
            "unavailable": len(unavailable),
            "available": len(domain_entities) - len(unavailable),
            "availability_pct": f"{availability_pct:.1f}%",
        },
        "recent_log_issues_count": len(log_issues),
        "recent_log_issues_sample": log_issues[-5:],
        "unavailable_sample": [e["entity_id"] for e in unavailable[:10]],
        "recommendations": recommendations,
    }


def _do_get_area_automation_summary(
    area_id: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    area_reg = load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])  # type: ignore[arg-type]
    dev_reg = load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])  # type: ignore[arg-type]
    ent_reg = load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])  # type: ignore[arg-type]

    area = None
    for a in area_reg:
        if a.get("id") == area_id or a.get("name", "").lower() == area_id.lower():
            area = a
            area_id = a.get("id")
            break

    if not area:
        return {"success": False, "error": f"Area '{area_id}' not found"}

    area_devs = {d["id"] for d in dev_reg if d.get("area_id") == area_id}

    area_ents = {
        e["entity_id"]
        for e in ent_reg
        if e.get("area_id") == area_id or e.get("device_id") in area_devs
    }

    if not area_ents:
        return {
            "success": False,
            "error": f"No entities found in area '{area.get('name')}'",
        }

    automations = []
    try:
        auto_path = Path(config_path) / "automations.yaml"  # type: ignore[arg-type]
        if auto_path.exists():
            with open(auto_path, encoding="utf-8") as f:
                auto_data = yaml.safe_load(f) or []

                for auto in auto_data:
                    auto_str = str(auto)
                    related_entities = []

                    for entity_id in area_ents:
                        if re.search(rf"\b{re.escape(entity_id)}\b", auto_str):
                            related_entities.append(entity_id)

                    if related_entities:
                        automations.append(
                            {
                                "alias": auto.get("alias", "Unknown"),
                                "id": auto.get("id"),
                                "related_entities_count": len(related_entities),
                                "sample_related": related_entities[:3],
                            }
                        )
    except Exception:
        pass

    live_states = []
    states_res = make_ha_request(ha_url, ha_token, "/api/states")  # type: ignore[arg-type]
    if states_res["success"]:
        live_states = [s for s in states_res["data"] if s["entity_id"] in area_ents]

    sensors = [s for s in live_states if s["entity_id"].startswith("sensor.")]
    binary_sensors = [s for s in live_states if s["entity_id"].startswith("binary_sensor.")]
    lights = [s for s in live_states if s["entity_id"].startswith("light.")]
    active_lights = [s for s in lights if s["state"] == "on"]
    switches = [s for s in live_states if s["entity_id"].startswith("switch.")]
    active_switches = [s for s in switches if s["state"] == "on"]

    temp_sensor = next(
        (s for s in sensors if s.get("attributes", {}).get("device_class") == "temperature"),
        None,
    )

    motion_sensors = [
        s for s in binary_sensors if s.get("attributes", {}).get("device_class") == "motion"
    ]
    motion_detected = any(s["state"] == "on" for s in motion_sensors)

    return {
        "success": True,
        "area": {
            "id": area_id,
            "name": area.get("name"),
            "aliases": area.get("aliases", []),
        },
        "intelligence": {
            "total_devices": len(area_devs),
            "total_entities": len(area_ents),
            "linked_automations": len(automations),
            "sensors_count": len(sensors),
            "lights_count": len(lights),
            "active_lights": len(active_lights),
        },
        "automations": automations[:10],
        "current_state_summary": {
            "lights_on": len(active_lights) > 0,
            "switches_on": len(active_switches) > 0,
            "motion_detected": motion_detected,
            "temperature": temp_sensor["state"] if temp_sensor else "N/A",
            "temperature_unit": temp_sensor.get("attributes", {}).get("unit_of_measurement")
            if temp_sensor
            else None,
        },
        "entity_breakdown": {
            "sensors": len(sensors),
            "binary_sensors": len(binary_sensors),
            "lights": len(lights),
            "switches": len(switches),
            "other": len(area_ents)
            - len(sensors)
            - len(binary_sensors)
            - len(lights)
            - len(switches),
        },
    }


def _do_get_notification_history(
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    states_res = make_ha_request(ha_url, ha_token, "/api/states")  # type: ignore[arg-type]
    active = []
    if states_res["success"]:
        for s in states_res["data"]:
            if s["entity_id"].startswith("persistent_notification."):
                active.append(
                    {
                        "entity_id": s["entity_id"],
                        "title": s["attributes"].get("title", "Unknown"),
                        "message": s["attributes"].get("message", "")[:200],
                        "created": s.get("last_changed"),
                        "notification_id": s["attributes"].get("notification_id"),
                    }
                )

    recent_notifications = []
    try:
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=24)

        logbook_res = make_ha_request(
            ha_url,  # type: ignore[arg-type]
            ha_token,  # type: ignore[arg-type]
            f"/api/logbook/{start_time.isoformat()}?end_time={end_time.isoformat()}",
        )

        if logbook_res["success"]:
            for entry in logbook_res["data"]:
                if entry.get("domain") == "notify":
                    recent_notifications.append(
                        {
                            "when": entry.get("when"),
                            "name": entry.get("name"),
                            "message": entry.get("message", "")[:100],
                        }
                    )
    except Exception:
        pass

    return {
        "success": True,
        "active_persistent_notifications": active,
        "active_count": len(active),
        "recent_notifications_24h": recent_notifications[-10:],
        "recent_count": len(recent_notifications),
    }


def _do_get_energy_dashboard_data(
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    states_res = make_ha_request(ha_url, ha_token, "/api/states")  # type: ignore[arg-type]
    if not states_res["success"]:
        return {"success": False, "error": "API error"}

    energy_sensors = []
    current_power = 0.0

    for s in states_res["data"]:
        attrs = s.get("attributes", {})
        state = s.get("state", "")

        if (
            attrs.get("device_class") == "energy"
            and state.replace(".", "", 1).replace("-", "", 1).isdigit()
        ):
            energy_sensors.append(s)

        if (
            attrs.get("device_class") == "power"
            and state.replace(".", "", 1).replace("-", "", 1).isdigit()
        ):
            try:
                current_power += float(state)
            except ValueError:
                pass

    now = datetime.now()
    hour = now.hour

    workday_sensor = next(
        (s for s in states_res["data"] if "workday" in s["entity_id"].lower()), None
    )

    is_workday = workday_sensor["state"] == "on" if workday_sensor else now.weekday() < 5

    tariff = "Off-Peak (Low Rate)"
    is_peak = False

    if is_workday:
        if (6 <= hour < 13) or (15 <= hour < 22):
            tariff = "Peak (High Rate)"
            is_peak = True

    rate_peak = 0.85
    rate_offpeak = 0.45

    price_peak_sensor = next(
        (
            s
            for s in states_res["data"]
            if "price" in s["entity_id"].lower() and "peak" in s["entity_id"].lower()
        ),
        None,
    )
    price_offpeak_sensor = next(
        (
            s
            for s in states_res["data"]
            if "price" in s["entity_id"].lower()
            and ("offpeak" in s["entity_id"].lower() or "off_peak" in s["entity_id"].lower())
        ),
        None,
    )

    if price_peak_sensor and price_peak_sensor["state"].replace(".", "", 1).isdigit():
        rate_peak = float(price_peak_sensor["state"])
    if price_offpeak_sensor and price_offpeak_sensor["state"].replace(".", "", 1).isdigit():
        rate_offpeak = float(price_offpeak_sensor["state"])

    current_rate = rate_peak if is_peak else rate_offpeak

    today_consumption_kwh = 0.0
    daily_sensors = [
        s
        for s in energy_sensors
        if "today" in s["entity_id"].lower() or "daily" in s["entity_id"].lower()
    ]

    for s in daily_sensors:
        try:
            today_consumption_kwh += float(s["state"])
        except ValueError:
            pass

    recommendations = []
    if is_peak:
        recommendations.append(
            {
                "priority": "high",
                "message": "PEAK! Avoid large consumers (washing machine, dishwasher, water heater)",
            }
        )
        recommendations.append(
            {
                "priority": "medium",
                "message": f"Savings: {rate_peak - rate_offpeak:.2f}/kWh if you shift to later",
            }
        )
    else:
        recommendations.append(
            {
                "priority": "info",
                "message": "Off-peak tariff - good time for large consumers",
            }
        )

        if hour < 6:
            recommendations.append(
                {
                    "priority": "info",
                    "message": f"Next peak in {6 - hour}h (06:00)",
                }
            )
        elif 13 <= hour < 15:
            recommendations.append(
                {
                    "priority": "info",
                    "message": f"Next peak in {15 - hour}h (15:00)",
                }
            )
        elif hour >= 22:
            recommendations.append(
                {
                    "priority": "info",
                    "message": "Off-peak rate until 06:00 AM",
                }
            )

    if current_power > 3000:
        recommendations.append(
            {
                "priority": "warning",
                "message": f"High consumption: {current_power:.0f}W",
            }
        )

    return {
        "success": True,
        "tariff_status": {
            "current_tariff": tariff,
            "is_peak": is_peak,
            "is_workday": is_workday,
            "current_rate_per_kwh": current_rate,
            "peak_rate": rate_peak,
            "offpeak_rate": rate_offpeak,
            "savings_potential": round(rate_peak - rate_offpeak, 2),
        },
        "consumption": {
            "current_power_w": round(current_power, 2),
            "today_energy_kwh": round(today_consumption_kwh, 2),
            "estimated_cost_today": round(today_consumption_kwh * current_rate, 2),
        },
        "sensors_found": {
            "energy_sensors": len(energy_sensors),
            "daily_sensors": len(daily_sensors),
            "price_sensors_configured": price_peak_sensor is not None
            and price_offpeak_sensor is not None,
        },
        "recommendations": recommendations,
    }


def _do_diagnose_person_tracking(
    person_entity: str = "person.test_user",
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    if not person_entity.startswith("person."):
        person_entity = f"person.{person_entity}"

    person_res = make_ha_request(ha_url, ha_token, f"/api/states/{person_entity}")  # type: ignore[arg-type]
    if not person_res["success"]:
        return {
            "success": False,
            "error": f"Person entity '{person_entity}' not found",
            "suggestion": "Use ha_search_entities(query='person') to list persons",
        }

    person_data = person_res["data"]
    person_state = person_data.get("state", "unknown")
    person_attrs = person_data.get("attributes", {})
    trackers = person_attrs.get("device_trackers", [])
    person_lat = person_attrs.get("latitude")
    person_lon = person_attrs.get("longitude")
    person_accuracy = person_attrs.get("gps_accuracy")
    person_source = person_attrs.get("source")

    zones_list = []
    try:
        ce_data = load_registry("core.config_entries", config_path)  # type: ignore[arg-type]
        for entry in ce_data.get("data", {}).get("entries", []):
            if entry.get("domain") == "zone":
                data = entry.get("data", {})
                zones_list.append(
                    {
                        "entity_id": f"zone.{entry.get('entry_id', '')}",
                        "name": entry.get("title", entry.get("entry_id", "")),
                        "latitude": data.get("latitude"),
                        "longitude": data.get("longitude"),
                        "radius": data.get("radius"),
                        "passive": data.get("passive", False),
                    }
                )
        if not zones_list:
            zone_reg = load_registry("zone", config_path)  # type: ignore[arg-type]
            for z in zone_reg.get("data", {}).get("items", []):
                zones_list.append(
                    {
                        "entity_id": f"zone.{z.get('id', '')}",
                        "name": z.get("name", z.get("id", "")),
                        "latitude": z.get("latitude"),
                        "longitude": z.get("longitude"),
                        "radius": z.get("radius"),
                        "passive": z.get("passive", False),
                    }
                )
    except Exception:
        pass

    current_zone = (
        person_state
        if person_state not in ["home", "not_home", "unknown", "unavailable"]
        else person_state
    )

    nearby_zones = []
    if person_lat and person_lon:
        for z in zones_list:
            z_lat = z.get("latitude")
            z_lon = z.get("longitude")
            if z_lat and z_lon:
                dx = (person_lat - z_lat) * 111320
                dy = (person_lon - z_lon) * 111320 * math.cos(math.radians(person_lat))
                dist = math.sqrt(dx**2 + dy**2)
                z_radius = z.get("radius", 100)
                if dist < z_radius + 500:
                    nearby_zones.append(
                        {
                            "entity_id": z["entity_id"],
                            "name": z["name"],
                            "distance_m": round(dist, 1),
                            "radius_m": z_radius,
                            "in_zone": dist <= z_radius,
                        }
                    )
        nearby_zones.sort(key=lambda x: x["distance_m"])

    states_result = make_ha_request(ha_url, ha_token, "/api/states")  # type: ignore[arg-type]
    states_map = {}
    if states_result.get("success"):
        states_map = {s["entity_id"]: s for s in states_result.get("data", [])}

    entity_reg = (
        load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])  # type: ignore[arg-type]
    )
    entity_to_platform = {e["entity_id"]: e.get("platform", "unknown") for e in entity_reg}
    entity_to_device = {e["entity_id"]: e.get("device_id") for e in entity_reg}

    device_reg = (
        load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])  # type: ignore[arg-type]
    )
    device_map = {}
    entry_domain_map = {}
    try:
        ce_data = load_registry("core.config_entries", config_path)  # type: ignore[arg-type]
        for e in ce_data.get("data", {}).get("entries", []):
            entry_domain_map[e.get("entry_id", "")] = e.get("domain", "unknown")
    except Exception:
        pass

    for d in device_reg:
        did = d.get("id", "")
        integration = "unknown"
        ce = d.get("config_entries", [])
        if ce:
            eid = (
                ce[0]
                if isinstance(ce, list)
                else (list(ce.keys())[0] if isinstance(ce, dict) else None)
            )
            if eid:
                integration = entry_domain_map.get(eid, eid)  # type: ignore[assignment]
        device_map[did] = {
            "name": d.get("name_by_user") or d.get("name") or did,
            "manufacturer": d.get("manufacturer"),
            "model": d.get("model"),
            "integration": integration,
        }

    tracker_details = []
    tracker_issues = []
    now_ts = time.time()

    for tracker_id in trackers:
        tracker_state = states_map.get(tracker_id, {})
        tracker_attrs = tracker_state.get("attributes", {}) if tracker_state else {}
        state_val = tracker_state.get("state", "unavailable") if tracker_state else "not_found"

        last_updated = tracker_state.get("last_updated") if tracker_state else None
        age_seconds = None
        is_stale = False
        staleness = "unknown"

        if last_updated:
            try:
                lu = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                age_seconds = now_ts - lu.timestamp()
                if age_seconds > 86400:
                    staleness = "stale_days"
                    is_stale = True
                elif age_seconds > 3600:
                    staleness = "stale_hours"
                    is_stale = True
                elif age_seconds > 1800:
                    staleness = "stale_30min"
                    is_stale = True
                elif age_seconds > 300:
                    staleness = "aging"
                else:
                    staleness = "fresh"
            except Exception:
                pass

        source_type = tracker_attrs.get("source_type", "unknown")
        accuracy = tracker_attrs.get("gps_accuracy")
        platform = entity_to_platform.get(tracker_id, tracker_id.split(".")[0])
        device_id = entity_to_device.get(tracker_id)
        device_info = device_map.get(device_id, {}) if device_id else {}

        detail = {
            "entity_id": tracker_id,
            "state": state_val,
            "source_type": source_type,
            "platform": platform,
            "gps_accuracy_m": accuracy,
            "age_seconds": round(age_seconds) if age_seconds is not None else None,
            "staleness": staleness,
            "last_updated": last_updated,
            "device_name": device_info.get("name", "unknown"),
            "integration": device_info.get("integration", platform),
        }
        if tracker_attrs.get("latitude"):
            detail["latitude"] = tracker_attrs["latitude"]
        if tracker_attrs.get("longitude"):
            detail["longitude"] = tracker_attrs["longitude"]

        tracker_details.append(detail)

        if is_stale:
            tracker_issues.append(
                {
                    "severity": "error" if staleness == "stale_days" else "warning",
                    "tracker": tracker_id,
                    "message": (
                        f"Tracker stale {round(age_seconds / 3600, 1)}h "  # type: ignore[operator]
                        f"— last update {last_updated}"
                    ),
                }
            )
        elif staleness == "aging":
            tracker_issues.append(
                {
                    "severity": "info",
                    "tracker": tracker_id,
                    "message": f"Tracker aging ({round(age_seconds / 60, 1)}min)",  # type: ignore[operator]
                }
            )

    related_automations = []
    try:
        auto_path = Path(config_path) / "automations.yaml"  # type: ignore[arg-type]
        if auto_path.exists():
            with open(auto_path, encoding="utf-8") as f:
                auto_data = yaml.safe_load(f) or []
            for auto in auto_data:
                auto_str = str(auto)
                if person_entity in auto_str:
                    alias = auto.get("alias", "Unknown")
                    auto_id = auto.get("id")
                    usage = []
                    if person_entity in str(auto.get("trigger", "")):
                        usage.append("trigger")
                    if person_entity in str(auto.get("condition", "")):
                        usage.append("condition")
                    if person_entity in str(auto.get("action", "")):
                        usage.append("action")
                    if not usage:
                        usage.append("config")
                    related_automations.append({"alias": alias, "id": auto_id, "usage": usage})
    except Exception:
        pass

    issues = []
    recommendations = []
    issues.extend(tracker_issues)

    if len(trackers) == 0:
        issues.append(
            {
                "severity": "error",
                "type": "no_trackers",
                "message": "No device trackers assigned",
            }
        )
        recommendations.append("Add at least one device_tracker to this person entity")
    elif len(trackers) == 1:
        issues.append(
            {
                "severity": "info",
                "type": "single_tracker",
                "message": "Only one tracker — no redundancy",
            }
        )

    active = [
        t
        for t in tracker_details
        if t["staleness"] not in ("stale_days", "stale_hours", "stale_30min")
    ]
    if not active and len(trackers) > 0:
        issues.append(
            {
                "severity": "error",
                "type": "all_stale",
                "message": "All trackers stale — location frozen",
            }
        )
        recommendations.append("Check HA Companion app: location permissions, battery optimization")

    if person_accuracy and person_accuracy > 50:
        issues.append(
            {
                "severity": "warning",
                "type": "low_accuracy",
                "message": f"GPS accuracy low ({person_accuracy}m)",
            }
        )

    if not issues:
        issues.append({"severity": "info", "type": "healthy", "message": "Healthy"})

    return {
        "success": True,
        "person": {
            "entity_id": person_entity,
            "state": person_state,
            "source": person_source,
            "latitude": person_lat,
            "longitude": person_lon,
            "gps_accuracy_m": person_accuracy,
            "last_changed": person_data.get("last_changed"),
            "last_updated": person_data.get("last_updated"),
        },
        "trackers": {
            "total": len(trackers),
            "active": len(active),
            "stale": len(trackers) - len(active),
            "details": tracker_details,
        },
        "zones": {
            "current": current_zone,
            "nearby": nearby_zones[:10],
            "total_configured": len(zones_list),
        },
        "automations": {
            "using_this_person": len(related_automations),
            "details": related_automations[:15],
        },
        "issues": issues,
        "recommendations": recommendations[:5],
    }


# ========================================
# SNAPSHOT STORAGE (for take/compare entity health snapshot)
# ========================================

_SNAPSHOTS: dict[str, dict[str, Any]] = {}


def _do_diagnose_connectivity(
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "overall_status": "healthy",
        "connectivity_issues": [],
        "retry_patterns": [],
        "recommendations": [],
    }

    health_res = make_ha_request(ha_url, ha_token, "/api/system_health")
    if health_res["success"]:
        health_data = health_res["data"]
        issues = []
        for integration, checks in health_data.items():
            if isinstance(checks, dict):
                for key, val in checks.items():
                    if isinstance(val, str) and any(
                        kw in val.lower() for kw in ["unavailable", "error", "fail", "retry"]
                    ):
                        issues.append({"integration": integration, "check": key, "detail": val})
                    elif isinstance(val, bool) and not val:
                        issues.append({"integration": integration, "check": key, "detail": "false"})
        if issues:
            result["overall_status"] = "degraded"
            result["connectivity_issues"] = issues

    if config_path:
        log_path = Path(config_path) / "home-assistant.log"
        if log_path.exists():
            retries = []
            try:
                with open(log_path, encoding="utf-8", errors="ignore") as f:
                    for line in f.readlines()[-5000:]:
                        if "retrying" in line.lower() or "connection refused" in line.lower():
                            retries.append(line.strip()[:200])
                if retries:
                    result["retry_patterns"] = retries[-20:]
                    if not result["connectivity_issues"]:
                        result["overall_status"] = "degraded"
                    result["recommendations"].append(
                        "Retry patterns detected — check network connectivity for affected integrations"
                    )
            except Exception:
                pass

    if not result["connectivity_issues"] and not result["retry_patterns"]:
        result["recommendations"].append("No connectivity issues detected")

    return result


def _do_diagnose_performance(
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "slowest_automations": [],
        "most_triggered": [],
        "largest_entities": [],
        "summary": "",
    }

    states_res = make_ha_request(ha_url, ha_token, "/api/states")
    if states_res["success"]:
        entities_with_attrs = [
            s
            for s in states_res["data"]
            if isinstance(s.get("attributes"), dict) and len(s["attributes"]) > 10
        ]
        entities_with_attrs.sort(key=lambda x: len(x.get("attributes", {})), reverse=True)
        result["largest_entities"] = [
            {
                "entity_id": e["entity_id"],
                "state": e.get("state"),
                "attribute_count": len(e.get("attributes", {})),
            }
            for e in entities_with_attrs[:10]
        ]

        automation_states = [
            s
            for s in states_res["data"]
            if s["entity_id"].startswith("automation.") and s.get("state") != "unavailable"
        ]
        automation_states.sort(
            key=lambda x: x.get("attributes", {}).get("last_triggered") or "",
        )
        result["slowest_automations"] = [
            {
                "entity_id": a["entity_id"],
                "state": a.get("state"),
                "last_triggered": a.get("attributes", {}).get("last_triggered"),
                "note": "Execution timing requires HA trace API; listed by stalest last_triggered",
            }
            for a in automation_states[:10]
        ]

    logbook_res = make_ha_request(
        ha_url,
        ha_token,
        f"/api/logbook/{(datetime.now() - timedelta(hours=24)).isoformat()}",
    )
    if logbook_res.get("success"):
        trigger_counts: Counter[str] = Counter()
        for entry in logbook_res.get("data", []):
            eid = entry.get("entity_id", "")
            if isinstance(eid, str) and eid.startswith("automation."):
                trigger_counts[eid] += 1
        most_common = trigger_counts.most_common(10)
        result["most_triggered"] = [
            {"entity_id": eid, "trigger_count": count} for eid, count in most_common
        ]

    if config_path:
        auto_path = Path(config_path) / "automations.yaml"
        if auto_path.exists():
            try:
                with open(auto_path, encoding="utf-8") as f:
                    auto_data = yaml.safe_load(f) or []
                result["summary"] = f"Found {len(auto_data)} automations"
            except Exception:
                pass

    if (
        not result["slowest_automations"]
        and not result["largest_entities"]
        and not result["most_triggered"]
    ):
        result["summary"] = (
            "Insufficient data for performance analysis. Enable tracing for automation timing."
        )

    return result


def _do_diagnose_startup_progress(
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "entity_count": 0,
        "integrations_loaded": 0,
        "integrations_total": 0,
        "automations_on": 0,
        "automations_total": 0,
        "recent_errors": 0,
        "progress_pct": 0.0,
        "status": "unknown",
    }

    states_res = make_ha_request(ha_url, ha_token, "/api/states")
    if states_res["success"]:
        result["entity_count"] = len(states_res["data"])

    config_res = make_ha_request(ha_url, ha_token, "/api/config")
    if config_res["success"]:
        components = config_res["data"].get("components", [])
        result["integrations_loaded"] = len(components)

    if config_path:
        entries = load_registry("core.config_entries", config_path)
        all_entries = entries.get("data", {}).get("entries", [])
        result["integrations_total"] = len(all_entries)
        loaded = sum(1 for e in all_entries if e.get("state") == "loaded")
        result["integrations_loaded"] = max(result["integrations_loaded"], loaded)

        auto_path = Path(config_path) / "automations.yaml"
        if auto_path.exists():
            try:
                with open(auto_path, encoding="utf-8") as f:
                    auto_data = yaml.safe_load(f) or []
                result["automations_total"] = len(auto_data)
            except Exception:
                pass

    if states_res["success"]:
        result["automations_on"] = sum(
            1
            for s in states_res["data"]
            if s["entity_id"].startswith("automation.") and s.get("state") == "on"
        )

    if config_path:
        log_path = Path(config_path) / "home-assistant.log"
        if log_path.exists():
            try:
                with open(log_path, encoding="utf-8", errors="ignore") as f:
                    for line in f.readlines()[-1000:]:
                        if "ERROR" in line:
                            result["recent_errors"] += 1
            except Exception:
                pass

    total = result["integrations_total"]
    loaded = result["integrations_loaded"]
    result["progress_pct"] = round(loaded / max(total, 1) * 100, 1)

    if total == 0:
        result["status"] = "starting"
    elif result["progress_pct"] < 80:
        result["status"] = "loading"
    else:
        result["status"] = "ready"

    return result


def _do_take_entity_health_snapshot(
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    states_res = make_ha_request(ha_url, ha_token, "/api/states")
    if not states_res["success"]:
        return {"success": False, "error": "Cannot fetch states"}

    all_states = states_res["data"]
    unavailable = [s for s in all_states if s.get("state") in ("unavailable", "unknown")]

    entity_reg_data = load_registry("core.entity_registry", config_path)
    entity_reg = entity_reg_data.get("data", {}).get("entities", [])

    by_integration: dict[str, int] = defaultdict(int)
    for s in unavailable:
        eid = s["entity_id"]
        platform = "unknown"
        for e in entity_reg:
            if e.get("entity_id") == eid:
                platform = e.get("platform", "unknown")
                break
        by_integration[platform] += 1

    unavailable_entity_ids = [s["entity_id"] for s in unavailable]

    snapshot_id = f"snap_{int(time.time())}"
    _SNAPSHOTS[snapshot_id] = {
        "timestamp": time.time(),
        "total_entities": len(all_states),
        "unavailable_count": len(unavailable),
        "unavailable_by_integration": dict(by_integration),
        "unavailable_entity_ids": unavailable_entity_ids,
    }

    return {
        "success": True,
        "snapshot_id": snapshot_id,
        "total_entities": len(all_states),
        "unavailable_count": len(unavailable),
        "unavailable_by_integration": dict(by_integration),
    }


def _do_compare_entity_health_snapshot(
    snapshot_id: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    snapshot = _SNAPSHOTS.get(snapshot_id)
    if not snapshot:
        return {"success": False, "error": f"Snapshot '{snapshot_id}' not found"}

    states_res = make_ha_request(ha_url, ha_token, "/api/states")
    if not states_res["success"]:
        return {"success": False, "error": "Cannot fetch current states"}

    prev_unavailable_ids = set(snapshot.get("unavailable_entity_ids", []))
    current_state_map = {s["entity_id"]: s.get("state") for s in states_res["data"]}
    current_unavailable_ids = {
        eid for eid, state in current_state_map.items() if state in ("unavailable", "unknown")
    }

    new_unavailable = sorted(current_unavailable_ids - prev_unavailable_ids)
    resolved = sorted(prev_unavailable_ids - current_unavailable_ids)
    unchanged = sorted(prev_unavailable_ids & current_unavailable_ids)

    return {
        "success": True,
        "snapshot_id": snapshot_id,
        "snapshot_time": snapshot.get("timestamp"),
        "new_unavailable": new_unavailable,
        "new_unavailable_count": len(new_unavailable),
        "resolved": resolved,
        "resolved_count": len(resolved),
        "unchanged": unchanged,
        "unchanged_count": len(unchanged),
    }


def _do_diagnose_voice(
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "exposed_entities_count": 0,
        "assistants_available": [],
        "pipelines": [],
        "issues": [],
        "recommendations": [],
    }

    states_res = make_ha_request(ha_url, ha_token, "/api/states")
    if states_res["success"]:
        stt = [s for s in states_res["data"] if s["entity_id"].startswith("stt.")]
        tts = [s for s in states_res["data"] if s["entity_id"].startswith("tts.")]
        conv = [s for s in states_res["data"] if s["entity_id"].startswith("conversation.")]

        result["assistants_available"] = {
            "stt": [s["entity_id"] for s in stt[:5]],
            "tts": [t["entity_id"] for t in tts[:5]],
            "conversation": [c["entity_id"] for c in conv[:5]],
        }

    exposure_res = make_ha_request(ha_url, ha_token, "/api/expose_entity")
    if exposure_res["success"]:
        exposed = exposure_res["data"]
        if isinstance(exposed, dict):
            result["exposed_entities_count"] = len(exposed.get("exposed_entities", {}))

    if config_path:
        pipe_data = load_registry("core.voice_assistant", config_path).get("data", {})
        if pipe_data:
            result["pipelines"] = pipe_data.get("pipelines", pipe_data.get("items", []))

    if not result["assistants_available"].get("conversation"):
        result["issues"].append("No conversation agents found — voice control may be unavailable")

    return result


def _do_diagnose_installation_type(
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "unknown",
        "version": "",
        "constraints": [],
        "available_features": [],
    }

    supervisor_res = make_ha_request(ha_url, ha_token, "/api/supervisor/info")
    if supervisor_res["success"]:
        info = supervisor_res["data"]
        result["type"] = info.get("type", "os")
        result["available_features"] = ["supervisor", "addons", "backups"]
    else:
        config_res = make_ha_request(ha_url, ha_token, "/api/config")
        if config_res["success"]:
            cfg = config_res["data"]
            result["version"] = cfg.get("version", "")
            result["type"] = "container"
            result["constraints"].append("No Supervisor — addons and snapshots unavailable")
        else:
            result["type"] = "core"
            result["constraints"].append("Minimal installation — manual updates required")

    if result["type"] == "os":
        result["available_features"] = [
            "supervisor",
            "addons",
            "backups",
            "OTA updates",
            "full system control",
        ]
    elif result["type"] == "supervised":
        result["available_features"] = ["supervisor", "addons", "backups", "managed by host OS"]
    elif result["type"] == "container":
        result["available_features"] = ["docker compose updates", "manual addons"]
    elif result["type"] == "core":
        result["available_features"] = ["pip/venv updates", "minimal footprint"]

    if not result["version"]:
        config_res = make_ha_request(ha_url, ha_token, "/api/config")
        if config_res["success"]:
            result["version"] = config_res["data"].get("version", "")

    return result


_KNOWN_FRAGILE_INTEGRATIONS = [
    "tuya_local",
    "gree",
    "xiaomi_miot",
    "googlefindmy",
    "pstryk",
    "kontomierz",
]


def _do_diagnose_post_update_integrations(
    ha_url: str | None = None,
    ha_token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "custom_components_total": 0,
        "loaded": 0,
        "failed": 0,
        "fragile_highlighted": [],
        "per_integration": {},
        "recent_errors": [],
        "recommendations": [],
    }

    entries_data = load_registry("core.config_entries", config_path)
    all_entries = entries_data.get("data", {}).get("entries", [])

    builtin_domains = {
        "homeassistant",
        "automation",
        "script",
        "scene",
        "group",
        "input_boolean",
        "input_number",
        "input_select",
        "input_text",
        "input_datetime",
        "input_button",
        "counter",
        "timer",
        "schedule",
        "template",
        "zone",
        "person",
        "tag",
        "sun",
        "weather",
        "sensor",
        "binary_sensor",
        "switch",
        "light",
        "climate",
        "cover",
        "lock",
        "fan",
        "vacuum",
        "media_player",
        "camera",
        "alarm_control_panel",
        "device_tracker",
        "stt",
        "tts",
        "conversation",
        "notify",
        "update",
        "system_log",
        "persistent_notification",
        "history",
        "recorder",
        "logbook",
        "energy",
        "mobile_app",
        "default_config",
        "dhcp",
        "usb",
        "zeroconf",
        "ssdp",
        "mqtt",
        "http",
        "api",
        "config",
        "frontend",
        "lovelace",
        "map",
        "image",
        "stream",
        "ffmpeg",
        "hls",
        "generic",
        "demo",
        "discovery",
        "system_health",
    }

    custom = [e for e in all_entries if e.get("domain") not in builtin_domains]
    result["custom_components_total"] = len(custom)

    fragile_found = []
    for e in custom:
        domain = e.get("domain", "unknown")
        entry_state = e.get("state", "unknown")
        entry_id = e.get("entry_id", "")
        title = e.get("title", "")

        if domain in _KNOWN_FRAGILE_INTEGRATIONS:
            fragile_found.append(
                {
                    "domain": domain,
                    "title": title,
                    "state": entry_state,
                }
            )

        per = result["per_integration"].get(
            domain,
            {  # type: ignore[attr-defined]
                "total": 0,
                "loaded": 0,
                "failed": 0,
                "entries": [],
            },
        )
        per["total"] += 1  # type: ignore[operator]
        if entry_state == "loaded":
            per["loaded"] += 1  # type: ignore[operator]
            result["loaded"] += 1  # type: ignore[operator]
        else:
            per["failed"] += 1  # type: ignore[operator]
            result["failed"] += 1  # type: ignore[operator]
        per["entries"].append({"entry_id": entry_id, "title": title, "state": entry_state})  # type: ignore[attr-defined]
        result["per_integration"][domain] = per  # type: ignore[attr-defined]

    if fragile_found:
        result["fragile_highlighted"] = fragile_found
        result["recommendations"].append(
            "Known-fragile integrations detected — check each after updates"
        )

    if config_path:
        log_path = Path(config_path) / "home-assistant.log"
        if log_path.exists():
            try:
                with open(log_path, encoding="utf-8", errors="ignore") as f:
                    for line in f.readlines()[-2000:]:
                        for e in custom:
                            if e.get("domain", "") in line and (
                                "ERROR" in line or "WARNING" in line
                            ):
                                result["recent_errors"].append(line.strip()[:200])
                                break
                result["recent_errors"] = result["recent_errors"][:20]
            except Exception:
                pass

    if result["failed"] > 0:
        result["recommendations"].append(
            f"{result['failed']} integration(s) failed to load — may be incompatible with current HA version"
        )

    if not result["recommendations"]:
        result["recommendations"].append("All custom integrations appear healthy")

    return result


# ========================================
# TOOL REGISTRATION
# ========================================


def register_diagnostics_tools(mcp, ha_url, ha_token, config_path) -> None:  # type: ignore[no-untyped-def]

    @mcp.tool()
    def diagnose_system_health(
        include_log_analysis: bool = True,
        include_unavailable_breakdown: bool = True,
        include_performance: bool = True,
        hours_back: int = 1,
    ) -> str:
        """[READ] Complete system diagnostics: health score, error patterns, unavailable entities, performance issues, and recommendations in a single call.

        Provides everything needed for AI-driven diagnosis:
        - Health Score and summary
        - Top error patterns with affected entities
        - Unavailable entities grouped by integration
        - Slow entities (performance issues)
        - API errors
        - Recommendations

        Args:
            include_log_analysis: Whether to analyze logs (default: True)
            include_unavailable_breakdown: Whether to group unavailable by integration (default: True)
            include_performance: Whether to detect slow entities (default: True)
            hours_back: How many hours back to analyze (1-24, default: 1)

        Returns:
            JSON with full diagnostics (~2k tokens instead of ~17k)
        """
        try:
            result = _do_diagnose_system_health(
                include_log_analysis=include_log_analysis,
                include_unavailable_breakdown=include_unavailable_breakdown,
                include_performance=include_performance,
                hours_back=hours_back,
                ha_url=ha_url,
                ha_token=ha_token,
                config_path=config_path,
            )
            _set_cache(
                f"diagnose_full_{include_log_analysis}_{include_unavailable_breakdown}_{include_performance}_{hours_back}",
                result,
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_system_health failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_unavailable_entities_grouped(
        group_by: str = "integration",
        include_device_names: bool = True,
        max_sample_entities: int = 5,
    ) -> str:
        """[READ] Group unavailable entities by integration or domain for efficient health analysis. ~95% token savings vs listing all.

        Instead of returning 200+ entities (48k tokens), groups them by integration (~2k tokens).

        Args:
            group_by: "integration" or "domain" (default: "integration")
            include_device_names: Whether to include device names (default: True)
            max_sample_entities: How many sample entities per group (default: 5)

        Returns:
            JSON with grouped entities and statistics.
        """
        try:
            cache_key = (
                f"unavailable_grouped_{group_by}_{include_device_names}_{max_sample_entities}"
            )
            result = _do_get_unavailable_entities_grouped(
                group_by=group_by,
                include_device_names=include_device_names,
                max_sample_entities=max_sample_entities,
                ha_url=ha_url,
                ha_token=ha_token,
                config_path=config_path,
            )
            _set_cache(cache_key, result)
            return _success_response(result)
        except Exception as exc:
            _logger.exception("get_unavailable_entities_grouped failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_integration_health(domain: str) -> str:
        """[READ] Get detailed health status of a specific integration domain (e.g. "mqtt", "tuya", "zha").

        Args:
            domain: Integration domain (e.g. "mqtt", "tuya", "zha").
        """
        try:
            result = _do_get_integration_health(
                domain=domain,
                ha_url=ha_url,
                ha_token=ha_token,
                config_path=config_path,
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("get_integration_health failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_area_automation_summary(area_id: str) -> str:
        """[READ] Analyze what is happening in a room: devices, automations related to those devices, and recent activity.

        Args:
            area_id: Area id (e.g. "living_room") or name (e.g. "Living Room").
        """
        try:
            result = _do_get_area_automation_summary(
                area_id=area_id,
                ha_url=ha_url,
                ha_token=ha_token,
                config_path=config_path,
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("get_area_automation_summary failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_notification_history() -> str:
        """[READ] Check active persistent notifications in Home Assistant."""
        try:
            result = _do_get_notification_history(
                ha_url=ha_url,
                ha_token=ha_token,
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("get_notification_history failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_energy_dashboard_data() -> str:
        """[READ] Get energy dashboard summary: current power, tariff info, daily consumption, and cost analysis."""
        try:
            result = _do_get_energy_dashboard_data(
                ha_url=ha_url,
                ha_token=ha_token,
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("get_energy_dashboard_data failed")
            return _error_response(str(exc))

    @mcp.tool()
    async def diagnose_person_tracking(person_entity: str = "person.test_user") -> str:
        """[READ] Full person tracking diagnostics: entity state, tracker freshness, zone proximity, and related automations. ~85% token savings.

        Aggregates ~6 individual calls into one:
        - Person entity state + device trackers
        - Each tracker: state, freshness, accuracy, integration type
        - Zones near the person (entered/at boundary)
        - Automations using this person entity
        - Recommendations for tracking reliability

        Args:
            person_entity: Person entity_id (default: "person.test_user")

        Returns:
            JSON with person state, tracker details, zones, automations, issues.
        """
        try:
            result = _do_diagnose_person_tracking(
                person_entity=person_entity,
                ha_url=ha_url,
                ha_token=ha_token,
                config_path=config_path,
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_person_tracking failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_connectivity() -> str:
        """[READ] Diagnose network and API connectivity issues across integrations. Checks system health and log retry patterns.

        Args:
            (none)

        Returns:
            JSON with overall_status, connectivity_issues list, retry_patterns, and recommendations.
        """
        try:
            result = _do_diagnose_connectivity(
                ha_url=ha_url, ha_token=ha_token, config_path=config_path
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_connectivity failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_performance() -> str:
        """[READ] Analyze system performance: slow automations, entities with large attribute payloads, and trigger frequency.

        Args:
            (none)

        Returns:
            JSON with slowest_automations, largest_entities, and summary.
        """
        try:
            result = _do_diagnose_performance(
                ha_url=ha_url, ha_token=ha_token, config_path=config_path
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_performance failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_startup_progress() -> str:
        """[READ] Estimate Home Assistant startup progress by counting entities, loaded integrations, and active automations.

        Args:
            (none)

        Returns:
            JSON with entity_count, integrations_loaded/total, automations_on/total, progress_pct, and status.
        """
        try:
            result = _do_diagnose_startup_progress(
                ha_url=ha_url, ha_token=ha_token, config_path=config_path
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_startup_progress failed")
            return _error_response(str(exc))

    @mcp.tool()
    def take_entity_health_snapshot() -> str:
        """[READ] Take a snapshot of current entity health (unavailable entities grouped by integration) for later comparison.

        Args:
            (none)

        Returns:
            JSON with snapshot_id, total_entities, unavailable_count, and unavailable_by_integration.
        """
        try:
            result = _do_take_entity_health_snapshot(
                ha_url=ha_url, ha_token=ha_token, config_path=config_path
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("take_entity_health_snapshot failed")
            return _error_response(str(exc))

    @mcp.tool()
    def compare_entity_health_snapshot(snapshot_id: str) -> str:
        """[READ] Compare a previously taken health snapshot with the current state. Shows new and resolved unavailable entities.

        Args:
            snapshot_id: Snapshot ID from take_entity_health_snapshot (e.g. "snap_1705312800").

        Returns:
            JSON with new_unavailable_sample, new_unavailable_count, resolved_count, and unchanged_count.
        """
        try:
            result = _do_compare_entity_health_snapshot(
                snapshot_id=snapshot_id,
                ha_url=ha_url,
                ha_token=ha_token,
                config_path=config_path,
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("compare_entity_health_snapshot failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_voice() -> str:
        """[READ] Diagnose voice assistant configuration: exposed entities, available STT/TTS/conversation agents, and pipelines.

        Args:
            (none)

        Returns:
            JSON with exposed_entities_count, assistants_available, pipelines, issues, and recommendations.
        """
        try:
            result = _do_diagnose_voice(ha_url=ha_url, ha_token=ha_token, config_path=config_path)
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_voice failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_installation_type() -> str:
        """[READ] Detect Home Assistant installation type (OS, Supervised, Container, or Core) and list feature constraints.

        Args:
            (none)

        Returns:
            JSON with type, version, constraints list, and available_features list.
        """
        try:
            result = _do_diagnose_installation_type(ha_url=ha_url, ha_token=ha_token)
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_installation_type failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_post_update_integrations() -> str:
        """[READ] Check custom integrations after a Home Assistant update. Highlights known-fragile integrations and load failures.

        Args:
            (none)

        Returns:
            JSON with custom_components_total, loaded/failed counts, fragile_highlighted, per_integration details, and recommendations.
        """
        try:
            result = _do_diagnose_post_update_integrations(
                ha_url=ha_url, ha_token=ha_token, config_path=config_path
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_post_update_integrations failed")
            return _error_response(str(exc))

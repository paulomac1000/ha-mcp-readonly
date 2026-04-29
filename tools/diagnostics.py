"""
System Diagnostics Tools
High-level tools for analyzing Home Assistant health, integration status, and energy usage.
Aggregates data from multiple sources (API, Registries, Logs) to save AI tokens.

Optimizations:
- diagnose_system_health() now contains everything in a single call (~90% token savings)
- get_unavailable_entities_grouped() groups entities instead of listing (~95% savings)
- TTL cache for recurring queries
"""

import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.utils import load_registry, make_ha_request

# ========================================
# 🗄️ CACHE CONFIGURATION
# ========================================

_DIAGNOSTICS_CACHE: Dict[str, Tuple[Any, float]] = {}
_CACHE_TTL = 60  # seconds


def _get_cached(key: str) -> Optional[Any]:
    """Returns data from cache if it is current."""
    if key in _DIAGNOSTICS_CACHE:
        data, timestamp = _DIAGNOSTICS_CACHE[key]
        if time.time() - timestamp < _CACHE_TTL:
            return data
    return None


def _set_cache(key: str, data: Any) -> None:
    """Writes data to cache with timestamp."""
    _DIAGNOSTICS_CACHE[key] = (data, time.time())


def _clear_cache() -> None:
    """Clears entire cache (used in tests)."""
    _DIAGNOSTICS_CACHE.clear()


def register_diagnostics_tools(mcp, ha_url, ha_token, config_path):

    # ========================================
    # 🛠️ INTERNAL HELPERS
    # ========================================

    def _get_log_errors_with_patterns(hours: int = 1, max_patterns: int = 10) -> Dict[str, Any]:
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
        error_patterns: Dict[str, Dict] = defaultdict(
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
        api_errors: List[Dict] = []
        slow_entities: List[Dict] = []

        cutoff = datetime.now() - timedelta(hours=hours)

        # Regex patterns
        entity_pattern = re.compile(
            r"\b(sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|"
            r"person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|"
            r"timer|counter|number|select|button|scene|group|alarm_control_panel)\.[a-zA-Z0-9_]+\b"
        )
        timestamp_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
        component_pattern = re.compile(r"\[([^\]]+)\]")
        slow_pattern = re.compile(
            r"took (\d+\.?\d*)\s*(?:seconds|s)|(\d+\.?\d*)\s*(?:seconds|s) to"
        )
        api_error_pattern = re.compile(
            r"(4\d{2}|5\d{2})\s*(Rate Limit|Unauthorized|Forbidden|Not Found|Server Error|timeout)",
            re.IGNORECASE,
        )

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-10000:]  # More lines for better analysis

                for line in lines:
                    is_error = "ERROR" in line
                    is_warning = "WARNING" in line

                    if not is_error and not is_warning:
                        continue

                    # Parsowanie daty
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
                    pattern_key = re.sub(
                        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+", "TIMESTAMP", line
                    )
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
                        # Clean message
                        msg_start = line.find("]") + 1 if "]" in line else 0
                        pattern_data["sample_message"] = line[msg_start : msg_start + 200].strip()

                    # Detect API errors
                    api_match = api_error_pattern.search(line)
                    if api_match:
                        http_code = api_match.group(1)
                        error_type = api_match.group(2).lower().replace(" ", "_")

                        # Find or create API error entry
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
                            if duration > 0.5:  # Threshold: 500ms
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

        # Sort and limit patterns
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
                    "affected_entities": list(data["affected_entities"])[:10],  # Limit
                    "first_seen": data["first_seen"],
                    "last_seen": data["last_seen"],
                }
            )

        # Sort slow entities by time
        slow_entities.sort(key=lambda x: x["max_time"], reverse=True)

        return {
            "errors": errors,
            "warnings": warnings,
            "top_error_patterns": top_patterns,
            "api_errors": api_errors[:10],  # Top 10
            "slow_entities": slow_entities[:10],  # Top 10
        }

    def _get_unavailable_by_integration(
        states: List[Dict], entity_registry: List[Dict], device_registry: List[Dict]
    ) -> Dict[str, Any]:
        """
        Groups unavailable entities by integration.
        Returns summary instead of full list of entities.
        """
        unavailable_states = [s for s in states if s["state"] in ["unavailable", "unknown"]]

        # Filter out ignorable entities
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

        # Build entity -> platform/integration mapping
        entity_to_platform = {}
        entity_to_device = {}
        for e in entity_registry:
            entity_to_platform[e.get("entity_id", "")] = e.get("platform", "unknown")
            entity_to_device[e.get("entity_id", "")] = e.get("device_id")

        # Build device -> name mapping
        device_to_name = {}
        for d in device_registry:
            device_id = d.get("id", "")
            name = d.get("name") or d.get("name_by_user") or "Unknown Device"
            device_to_name[device_id] = name

        # Group by integration
        by_integration: Dict[str, Dict] = defaultdict(
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

        # Convert to serializable format
        result = {}
        for platform, data in sorted(
            by_integration.items(), key=lambda x: x[1]["count"], reverse=True
        ):
            result[platform] = {
                "count": data["count"],
                "percentage": round(data["count"] / len(unavailable_states) * 100, 1)
                if unavailable_states
                else 0,
                "unique_devices": len(data["devices"]),
                "device_names": data["device_names"][:5],  # Limit
                "sample_entities": data["sample_entities"],
            }

        return {"total_unavailable": len(unavailable_states), "by_integration": result}

    def _get_by_domain(states: List[Dict]) -> Dict[str, int]:
        """Groups unavailable entities by domain."""
        unavailable = [s for s in states if s["state"] in ["unavailable", "unknown"]]
        domain_counts = Counter()
        for s in unavailable:
            domain = s["entity_id"].split(".")[0]
            domain_counts[domain] += 1
        return dict(domain_counts.most_common())

    # ========================================
    # 🩺 CORE DIAGNOSTICS - ROZSZERZONE
    # ========================================

    @mcp.tool()
    def diagnose_system_health(
        include_log_analysis: bool = True,
        include_unavailable_breakdown: bool = True,
        include_performance: bool = True,
        hours_back: int = 1,
    ) -> str:
        """
        🩺 COMPLETE SYSTEM DIAGNOSTICS - full audit in a single call.

        Zawiera WSZYSTKO czego AI potrzebuje do diagnozy:
        - Health Score i summary
        - Top error patterns z affected entities
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
        cache_key = f"diagnose_full_{include_log_analysis}_{include_unavailable_breakdown}_{include_performance}_{hours_back}"
        cached = _get_cached(cache_key)
        if cached:
            return cached

        hours_back = min(max(int(hours_back), 1), 24)

        # 1. Get states (API)
        states_res = make_ha_request(ha_url, ha_token, "/api/states")
        if not states_res["success"]:
            return json.dumps({"success": False, "error": "Cannot fetch system states"}, indent=2)

        states = states_res["data"]

        # 2. Pobierz registry dla lepszego grupowania
        entity_reg = (
            load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
        )
        device_reg = (
            load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
        )

        # 3. Basic stats
        total_entities = len(states)
        unavailable_raw = [s for s in states if s["state"] in ["unavailable", "unknown"]]

        # Filter ignorable
        def is_ignorable(entity_id: str) -> bool:
            ignorable_domains = ["sun", "weather", "calendar", "update"]
            domain = entity_id.split(".")[0]
            return domain in ignorable_domains

        unavailable = [s for s in unavailable_raw if not is_ignorable(s["entity_id"])]

        # 4. Log analysis (if enabled)
        log_analysis = {
            "errors": 0,
            "warnings": 0,
            "top_error_patterns": [],
            "api_errors": [],
            "slow_entities": [],
        }
        if include_log_analysis or include_performance:
            log_analysis = _get_log_errors_with_patterns(hours=hours_back)

        # 5. Unavailable grouping (if enabled)
        unavailable_breakdown = {}
        if include_unavailable_breakdown:
            unavailable_data = _get_unavailable_by_integration(states, entity_reg, device_reg)
            unavailable_breakdown = unavailable_data["by_integration"]

        # 6. Persistent notifications
        notifications = [s for s in states if s["entity_id"].startswith("persistent_notification.")]

        # 7. Oblicz Health Score (0-100)
        score = 100
        score -= min(len(unavailable) * 2, 40)
        score -= min(log_analysis["errors"] * 3, 30)  # Zmniejszone z 5 do 3
        score -= min(len(notifications) * 10, 20)  # Zmniejszone z 15 do 10
        score -= min(len(log_analysis.get("api_errors", [])) * 5, 10)
        score = max(0, score)

        # 8. Status
        if score >= 80:
            status = "Healthy"
        elif score >= 50:
            status = "Warning"
        else:
            status = "Critical"

        # 9. Recommendations (inteligentne)
        recommendations = []

        if score < 50:
            recommendations.append(
                {
                    "priority": "critical",
                    "message": "System health is CRITICAL. Immediate attention required.",
                }
            )

        # Top problematic integrations
        if unavailable_breakdown:
            top_integration = max(
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

        # API errors
        if log_analysis.get("api_errors"):
            for api_err in log_analysis["api_errors"][:2]:  # Top 2
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

        # Top error patterns
        if log_analysis.get("top_error_patterns"):
            top_pattern = log_analysis["top_error_patterns"][0]
            if top_pattern["count"] > 10:
                recommendations.append(
                    {
                        "priority": "high",
                        "message": f"Recurring error ({top_pattern['count']}x): {top_pattern['pattern'][:80]}...",
                    }
                )

        # Slow entities
        if log_analysis.get("slow_entities"):
            slow = log_analysis["slow_entities"][0]
            if slow.get("max_time", 0) > 2.0:
                recommendations.append(
                    {
                        "priority": "medium",
                        "message": f"Slow entity {slow['entity_id']}: {slow['max_time']:.1f}s response time.",
                    }
                )

        if log_analysis["errors"] > 20:
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
                    "message": "✅ System is healthy! No critical issues detected.",
                }
            )

        # 10. Build result
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

        # Remove None values for cleaner output
        result = {k: v for k, v in result.items() if v is not None}

        json_result = json.dumps(result, indent=2, ensure_ascii=False)
        _set_cache(cache_key, json_result)
        return json_result

    @mcp.tool()
    def get_unavailable_entities_grouped(
        group_by: str = "integration",
        include_device_names: bool = True,
        max_sample_entities: int = 5,
    ) -> str:
        """
        📊 GROUPED UNAVAILABLE ENTITIES - Effective summary of unavailable entities.

        Instead of returning 200+ entities (48k tokens), groups them by integration (~2k tokens).

        Args:
            group_by: "integration" or "domain" (default: "integration")
            include_device_names: Whether to include device names (default: True)
            max_sample_entities: How many sample entities per group (default: 5)

        Returns:
            JSON z pogrupowanymi entitymi i statystykami
        """
        cache_key = f"unavailable_grouped_{group_by}_{include_device_names}_{max_sample_entities}"
        cached = _get_cached(cache_key)
        if cached:
            return cached

        # 1. Get states
        states_res = make_ha_request(ha_url, ha_token, "/api/states")
        if not states_res["success"]:
            return json.dumps({"success": False, "error": "Cannot fetch states"}, indent=2)

        states = states_res["data"]

        # 2. Pobierz registry
        entity_reg = (
            load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
        )
        device_reg = (
            load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
        )

        # 3. Grupuj
        if group_by == "integration":
            grouped_data = _get_unavailable_by_integration(states, entity_reg, device_reg)

            # Limit sample entities
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
            # Group by domain
            unavailable = [s for s in states if s["state"] in ["unavailable", "unknown"]]
            by_domain: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "sample_entities": []})

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

        json_result = json.dumps(result, indent=2, ensure_ascii=False)
        _set_cache(cache_key, json_result)
        return json_result

    @mcp.tool()
    def get_integration_health(domain: str) -> str:
        """
        🔍 INTEGRATION HEALTH - detailed state of a specific integration.

        Args:
            domain: Integration domain (e.g. "mqtt", "tuya", "zha").
        """
        # 1. Get states
        states_res = make_ha_request(ha_url, ha_token, "/api/states")
        if not states_res["success"]:
            return json.dumps({"success": False, "error": "API error"}, indent=2)

        # 2. Get entity registry for platform mapping
        entity_reg_data = load_registry("core.entity_registry", config_path)
        entity_reg = entity_reg_data.get("data", {}).get("entities", [])

        # Find entities for this platform
        platform_entity_ids = {e["entity_id"] for e in entity_reg if e.get("platform") == domain}

        # Filtruj statey
        domain_entities = [
            s
            for s in states_res["data"]
            if s["entity_id"].startswith(f"{domain}.") or s["entity_id"] in platform_entity_ids
        ]

        if not domain_entities:
            return json.dumps(
                {
                    "success": False,
                    "domain": domain,
                    "status": "Not Found / No Entities",
                    "error": f"No entities found for domain '{domain}'",
                },
                indent=2,
            )

        unavailable = [e for e in domain_entities if e["state"] in ["unavailable", "unknown"]]

        # 2. Check logs for this domain
        log_issues = []
        log_path = Path(config_path) / "home-assistant.log"
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f.readlines()[-2000:]:
                        if domain.lower() in line.lower() and (
                            "ERROR" in line or "WARNING" in line
                        ):
                            log_issues.append(line.strip()[:200])
            except Exception:
                pass

        # 3. Status
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

        # 4. Recommendations
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

        return json.dumps(
            {
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
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    def get_area_automation_summary(area_id: str) -> str:
        """
        🏠 AREA INTELLIGENCE - analyzes what is happening in a given room.
        Checks devices, automations related to these devices, and their activity.

        Args:
            area_id: Area id (e.g. "salon") or name (e.g. "Salon").
        """
        # 1. Area -> Devices -> Entities mapping
        area_reg = load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])
        dev_reg = (
            load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
        )
        ent_reg = (
            load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
        )

        # Find area (by id or name)
        area = None
        for a in area_reg:
            if a.get("id") == area_id or a.get("name", "").lower() == area_id.lower():
                area = a
                area_id = a.get("id")
                break

        if not area:
            return json.dumps({"success": False, "error": f"Area '{area_id}' not found"}, indent=2)

        # Find devices in area
        area_devs = {d["id"] for d in dev_reg if d.get("area_id") == area_id}

        # Find entities
        area_ents = {
            e["entity_id"]
            for e in ent_reg
            if e.get("area_id") == area_id or e.get("device_id") in area_devs
        }

        if not area_ents:
            return json.dumps(
                {
                    "success": False,
                    "error": f"No entities found in area '{area.get('name')}'",
                },
                indent=2,
            )

        # 2. Find automations using these entities
        automations = []
        try:
            import yaml

            auto_path = Path(config_path) / "automations.yaml"
            if auto_path.exists():
                with open(auto_path, "r", encoding="utf-8") as f:
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

        # 3. Get live state for entities in the room
        live_states = []
        states_res = make_ha_request(ha_url, ha_token, "/api/states")
        if states_res["success"]:
            live_states = [s for s in states_res["data"] if s["entity_id"] in area_ents]

        # Statistics
        sensors = [s for s in live_states if s["entity_id"].startswith("sensor.")]
        binary_sensors = [s for s in live_states if s["entity_id"].startswith("binary_sensor.")]
        lights = [s for s in live_states if s["entity_id"].startswith("light.")]
        active_lights = [s for s in lights if s["state"] == "on"]
        switches = [s for s in live_states if s["entity_id"].startswith("switch.")]
        active_switches = [s for s in switches if s["state"] == "on"]

        # Find temperature
        temp_sensor = next(
            (s for s in sensors if s.get("attributes", {}).get("device_class") == "temperature"),
            None,
        )

        # Find motion
        motion_sensors = [
            s for s in binary_sensors if s.get("attributes", {}).get("device_class") == "motion"
        ]
        motion_detected = any(s["state"] == "on" for s in motion_sensors)

        return json.dumps(
            {
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
                "automations": automations[:10],  # Limit
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
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    def get_notification_history() -> str:
        """
        🔔 NOTIFICATION TRACKING - checks active notifications.
        """
        states_res = make_ha_request(ha_url, ha_token, "/api/states")
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

        # Historia z logbook
        recent_notifications = []
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=24)

            logbook_res = make_ha_request(
                ha_url,
                ha_token,
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

        return json.dumps(
            {
                "success": True,
                "active_persistent_notifications": active,
                "active_count": len(active),
                "recent_notifications_24h": recent_notifications[-10:],
                "recent_count": len(recent_notifications),
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    def get_energy_dashboard_data() -> str:
        """
        ⚡ ENERGY DASHBOARD (G12w) - energy summary.
        """
        states_res = make_ha_request(ha_url, ha_token, "/api/states")
        if not states_res["success"]:
            return json.dumps({"success": False, "error": "API error"}, indent=2)

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

        # Detect G12w tariff
        now = datetime.now()
        hour = now.hour

        workday_sensor = next(
            (s for s in states_res["data"] if "workday" in s["entity_id"].lower()), None
        )

        is_workday = workday_sensor["state"] == "on" if workday_sensor else now.weekday() < 5

        tariff = "G12w - Pozaszczyt (Tania)"
        is_peak = False

        if is_workday:
            if (6 <= hour < 13) or (15 <= hour < 22):
                tariff = "G12w - Szczyt (Droga)"
                is_peak = True

        # Prices
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

        # Today's consumption
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

        # Recommendations
        recommendations = []
        if is_peak:
            recommendations.append(
                {
                    "priority": "high",
                    "message": "⚠️ PEAK! Avoid large consumers (washing machine, dishwasher, water heater)",
                }
            )
            recommendations.append(
                {
                    "priority": "medium",
                    "message": f"Savings: {rate_peak - rate_offpeak:.2f} PLN/kWh if you shift to later",
                }
            )
        else:
            recommendations.append(
                {
                    "priority": "info",
                    "message": "✅ Cheap tariff - good time for large consumers",
                }
            )

            if hour < 6:
                recommendations.append(
                    {"priority": "info", "message": f"Next peak in {6 - hour}h (06:00)"}
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
                    {"priority": "info", "message": "Tania taryfa do 06:00 rano"}
                )

        if current_power > 3000:
            recommendations.append(
                {
                    "priority": "warning",
                    "message": f"High consumption: {current_power:.0f}W",
                }
            )

        return json.dumps(
            {
                "success": True,
                "tariff_status": {
                    "current_tariff": tariff,
                    "is_peak": is_peak,
                    "is_workday": is_workday,
                    "current_rate_pln_kwh": current_rate,
                    "peak_rate": rate_peak,
                    "offpeak_rate": rate_offpeak,
                    "savings_potential": round(rate_peak - rate_offpeak, 2),
                },
                "consumption": {
                    "current_power_w": round(current_power, 2),
                    "today_energy_kwh": round(today_consumption_kwh, 2),
                    "estimated_cost_today_pln": round(today_consumption_kwh * current_rate, 2),
                },
                "sensors_found": {
                    "energy_sensors": len(energy_sensors),
                    "daily_sensors": len(daily_sensors),
                    "price_sensors_configured": price_peak_sensor is not None
                    and price_offpeak_sensor is not None,
                },
                "recommendations": recommendations,
            },
            indent=2,
            ensure_ascii=False,
        )

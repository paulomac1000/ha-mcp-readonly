"""
Home Assistant Developer Tools
Advanced testing, validation, and diagnostics with batch operations.
Provides tools for testing Jinja2 templates, verifying entity existence, and debugging automations.
"""

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from tools.utils import _error_response, _success_response, make_ha_request

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


# =============================================================================
# MODULE-LEVEL INTERNAL HELPERS (_do_*)
# =============================================================================


def _do_test_template(template, timeout, report_errors, ha_url, ha_token):  # type: ignore[no-untyped-def]
    start_time = time.time()
    result = make_ha_request(
        ha_url,
        ha_token,
        "/api/template",
        method="POST",
        data={"template": template},
    )
    render_time = time.time() - start_time

    if not result["success"]:
        return {
            "success": False,
            "template": template,
            "error": result["error"],
            "render_time": f"{render_time:.3f}s",
        }

    return {
        "success": True,
        "template": template,
        "result": result["data"],
        "render_time": f"{render_time:.3f}s",
        "performance_warning": render_time > 0.5,
    }


def _do_test_templates_batch(templates, ha_url, ha_token):  # type: ignore[no-untyped-def]
    try:
        templates_data = json.loads(templates)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid JSON: {str(e)}"}

    if isinstance(templates_data, list):
        templates_dict = {f"template_{i}": t for i, t in enumerate(templates_data)}
    elif isinstance(templates_data, dict):
        templates_dict = templates_data
    else:
        return {"success": False, "error": "Templates must be a JSON array or object"}

    results = {
        "success": True,
        "total_templates": len(templates_dict),
        "successful": 0,
        "failed": 0,
        "total_time": 0,
        "results": [],
    }

    for name, template in templates_dict.items():
        start_time = time.time()
        test_result = make_ha_request(
            ha_url,
            ha_token,
            "/api/template",
            method="POST",
            data={"template": template},
        )
        render_time = time.time() - start_time
        results["total_time"] += render_time  # type: ignore[operator]

        if test_result["success"]:
            results["successful"] += 1  # type: ignore[operator]
            results["results"].append(  # type: ignore[attr-defined]
                {
                    "name": name,
                    "template": template,
                    "result": test_result["data"],
                    "render_time": f"{render_time:.3f}s",
                    "status": "OK",
                }
            )
        else:
            results["failed"] += 1  # type: ignore[operator]
            results["results"].append(  # type: ignore[attr-defined]
                {
                    "name": name,
                    "template": template,
                    "error": test_result["error"],
                    "render_time": f"{render_time:.3f}s",
                    "status": "FAILED",
                }
            )

    results["statistics"] = {
        "success_rate": f"{(results['successful'] / results['total_templates'] * 100):.1f}%",  # type: ignore[operator]
        "average_render_time": f"{(results['total_time'] / results['total_templates']):.3f}s",  # type: ignore[operator]
        "total_time": f"{results['total_time']:.3f}s",
        "slowest": max(  # type: ignore[call-overload]
            results["results"],
            key=lambda x: float(x["render_time"].replace("s", "")),
        )["name"]
        if results["results"]
        else None,
    }

    return results


def _do_eval_templates_batch(templates_str, mock_variables, ha_url, ha_token):  # type: ignore[no-untyped-def]
    try:
        templates_data = json.loads(templates_str)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid templates JSON: {str(e)}"}

    if isinstance(templates_data, list):
        templates_dict = {f"template_{i}": t for i, t in enumerate(templates_data)}
    elif isinstance(templates_data, dict):
        templates_dict = templates_data
    else:
        return {"success": False, "error": "templates must be a JSON array or object"}

    mock_prefix = ""
    if mock_variables:
        try:
            mock_dict = (
                json.loads(mock_variables) if isinstance(mock_variables, str) else mock_variables
            )
            if isinstance(mock_dict, dict):
                mock_parts = []
                for k, v in mock_dict.items():
                    if isinstance(v, str):
                        mock_parts.append(f"{{% set {k} = '{v}' %}}")
                    elif isinstance(v, bool):
                        mock_parts.append(f"{{% set {k} = {'true' if v else 'false'} %}}")
                    else:
                        mock_parts.append(f"{{% set {k} = {v} %}}")
                mock_prefix = "\n".join(mock_parts) + "\n"
        except (json.JSONDecodeError, TypeError):
            pass

    results = {"success": True, "total": 0, "successful": 0, "failed": 0, "results": {}}

    for name, template in templates_dict.items():
        full_template = mock_prefix + template
        site_result = make_ha_request(
            ha_url, ha_token, "/api/template", method="POST", data={"template": full_template}
        )

        if site_result["success"]:
            results["successful"] += 1
            results["results"][name] = {"result": site_result["data"], "error": None}
        else:
            results["failed"] += 1
            results["results"][name] = {"result": None, "error": site_result["error"]}

        results["total"] += 1

    return results


def _do_get_template_performance(template, iterations, ha_url, ha_token):  # type: ignore[no-untyped-def]
    iterations = min(max(int(iterations), 1), 20)
    times = []

    for _ in range(iterations):
        start = time.time()
        res = make_ha_request(
            ha_url,
            ha_token,
            "/api/template",
            method="POST",
            data={"template": template},
        )
        elapsed = (time.time() - start) * 1000
        times.append(elapsed)

        if not res["success"]:
            return {"success": False, "error": res["error"]}

    avg_time = sum(times) / len(times)

    complexity_score = 1
    if "expand" in template:
        complexity_score += 5
    if "states" in template and "states." not in template:
        complexity_score += 10
    if "regex" in template:
        complexity_score += 3
    if "log" in template:
        complexity_score += 2
    if len(template) > 500:
        complexity_score += 3

    assessment = "Fast"
    if avg_time > 50:
        assessment = "Moderate"
    if avg_time > 200:
        assessment = "Slow"
    if avg_time > 500:
        assessment = "Very Slow (Avoid in automations)"

    return {
        "success": True,
        "template": template[:100] + "..." if len(template) > 100 else template,
        "benchmark": {
            "iterations": iterations,
            "avg_ms": round(avg_time, 2),
            "min_ms": round(min(times), 2),
            "max_ms": round(max(times), 2),
            "std_dev_ms": round((sum((t - avg_time) ** 2 for t in times) / len(times)) ** 0.5, 2),
        },
        "analysis": {
            "assessment": assessment,
            "complexity_score": complexity_score,
            "memory_impact": "High" if complexity_score > 8 else "Low",
            "recommendations": [
                "Consider caching result in variable" if avg_time > 100 else None,
                "Avoid using in fast-polling automations" if avg_time > 200 else None,
                "Split into multiple simpler templates" if complexity_score > 10 else None,
            ],
        },
    }


def _do_validate_automation_trigger(trigger_config, ha_url, ha_token):  # type: ignore[no-untyped-def]
    import yaml

    try:
        trigger_data = yaml.safe_load(trigger_config)
    except yaml.YAMLError as e:
        return {
            "success": False,
            "valid": False,
            "error": f"YAML parsing error: {str(e)}",
        }

    try:
        if not isinstance(trigger_data, list):
            trigger_data = [trigger_data]

        issues = []
        warnings = []

        for idx, trigger in enumerate(trigger_data):
            if not isinstance(trigger, dict):
                issues.append(f"Trigger {idx}: Must be a dictionary")
                continue

            if "platform" not in trigger:
                issues.append(f"Trigger {idx}: Missing 'platform' key")
                continue

            platform = trigger.get("platform")

            if platform == "state":
                if "entity_id" not in trigger:
                    issues.append(f"Trigger {idx}: 'state' platform requires 'entity_id'")
                if "to" not in trigger and "from" not in trigger:
                    warnings.append(
                        f"Trigger {idx}: Consider adding 'to' or 'from' for more specific triggering"
                    )
                if "for" in trigger:
                    for_value = trigger["for"]
                    if isinstance(for_value, str) and not re.match(
                        r"^\d{2}:\d{2}:\d{2}$", for_value
                    ):
                        warnings.append(
                            f"Trigger {idx}: 'for' should be in HH:MM:SS format or use time object"
                        )

            elif platform == "time":
                if "at" not in trigger:
                    issues.append(f"Trigger {idx}: 'time' platform requires 'at'")
                else:
                    at_value = trigger["at"]
                    if isinstance(at_value, str) and not re.match(
                        r"^\d{2}:\d{2}(:\d{2})?$", at_value
                    ):
                        warnings.append(
                            f"Trigger {idx}: 'at' should be in HH:MM or HH:MM:SS format"
                        )

            elif platform == "numeric_state":
                if "entity_id" not in trigger:
                    issues.append(f"Trigger {idx}: 'numeric_state' requires 'entity_id'")
                if "above" not in trigger and "below" not in trigger:
                    issues.append(f"Trigger {idx}: 'numeric_state' requires 'above' or 'below'")

            elif platform == "template":
                if "value_template" not in trigger:
                    issues.append(f"Trigger {idx}: 'template' platform requires 'value_template'")
                else:
                    template = trigger["value_template"]
                    if not ("{{" in template or "{%" in template):
                        warnings.append(
                            f"Trigger {idx}: 'value_template' doesn't appear to contain Jinja2 syntax"
                        )

            elif platform == "event":
                if "event_type" not in trigger:
                    issues.append(f"Trigger {idx}: 'event' platform requires 'event_type'")

            elif platform == "homeassistant":
                if "event" not in trigger:
                    issues.append(
                        f"Trigger {idx}: 'homeassistant' platform requires 'event' (start/shutdown)"
                    )
                elif trigger["event"] not in ["start", "shutdown"]:
                    warnings.append(f"Trigger {idx}: 'event' should be 'start' or 'shutdown'")

            elif platform == "time_pattern":
                if not any(k in trigger for k in ["hours", "minutes", "seconds"]):
                    issues.append(
                        f"Trigger {idx}: 'time_pattern' requires at least one of: hours, minutes, seconds"
                    )

            elif platform == "webhook":
                if "webhook_id" not in trigger:
                    issues.append(f"Trigger {idx}: 'webhook' platform requires 'webhook_id'")

            elif platform == "zone":
                if "entity_id" not in trigger or "zone" not in trigger:
                    issues.append(f"Trigger {idx}: 'zone' platform requires 'entity_id' and 'zone'")
                if "event" not in trigger:
                    warnings.append(f"Trigger {idx}: Consider specifying 'event' (enter/leave)")

            elif platform == "sun":
                if "event" not in trigger:
                    issues.append(
                        f"Trigger {idx}: 'sun' platform requires 'event' (sunrise/sunset)"
                    )
                elif trigger["event"] not in ["sunrise", "sunset"]:
                    warnings.append(f"Trigger {idx}: 'event' should be 'sunrise' or 'sunset'")

            elif platform == "mqtt":
                if "topic" not in trigger:
                    issues.append(f"Trigger {idx}: 'mqtt' platform requires 'topic'")

            elif platform == "device":
                if "device_id" not in trigger:
                    issues.append(f"Trigger {idx}: 'device' platform requires 'device_id'")

            else:
                warnings.append(
                    f"Trigger {idx}: Unknown platform '{platform}' - validation skipped"
                )

        if issues:
            return {
                "success": False,
                "valid": False,
                "issues": issues,
                "warnings": warnings,
            }

        return {
            "success": True,
            "valid": True,
            "message": "Trigger configuration looks valid",
            "triggers": trigger_data,
            "warnings": warnings if warnings else None,
        }

    except Exception as e:
        return {
            "success": False,
            "valid": False,
            "error": f"Validation error: {str(e)}",
        }


def _do_test_condition(condition_template, context, ha_url, ha_token):  # type: ignore[no-untyped-def]
    full_template = condition_template
    if context:
        full_template = f"{{% set {context} %}}{condition_template}"

    start_time = time.time()
    result = make_ha_request(
        ha_url,
        ha_token,
        "/api/template",
        method="POST",
        data={"template": full_template},
    )
    render_time = time.time() - start_time

    if not result["success"]:
        return {
            "success": False,
            "condition": condition_template,
            "error": result["error"],
            "context": context,
        }

    result_value = result["data"]
    evaluates_to = None

    if isinstance(result_value, bool):
        evaluates_to = result_value
    elif isinstance(result_value, str):
        if result_value.lower() in ["true", "1", "yes", "on"]:
            evaluates_to = True
        elif result_value.lower() in ["false", "0", "no", "off"]:
            evaluates_to = False

    return {
        "success": True,
        "condition": condition_template,
        "result": result_value,
        "evaluates_to": evaluates_to,
        "context": context,
        "render_time": f"{render_time:.3f}s",
    }


def _do_check_entity_exists(entity_id, ha_url, ha_token):  # type: ignore[no-untyped-def]
    result = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")

    if not result["success"]:
        return {
            "success": False,
            "exists": False,
            "entity_id": entity_id,
            "error": result["error"],
        }

    state_data = result["data"]
    attributes = state_data.get("attributes", {})

    return {
        "success": True,
        "exists": True,
        "entity_id": entity_id,
        "current_state": state_data.get("state"),
        "friendly_name": attributes.get("friendly_name", ""),
        "device_class": attributes.get("device_class"),
        "unit_of_measurement": attributes.get("unit_of_measurement"),
        "last_changed": state_data.get("last_changed"),
        "last_updated": state_data.get("last_updated"),
    }


def _do_check_entities_batch(entity_ids, ha_url, ha_token):  # type: ignore[no-untyped-def]
    entity_list = [e.strip() for e in entity_ids.split(",") if e.strip()]

    result = {
        "success": True,
        "total_entities": len(entity_list),
        "exists_count": 0,
        "missing_count": 0,
        "unavailable_count": 0,
        "results": [],
        "issues": [],
    }

    states_result = make_ha_request(ha_url, ha_token, "/api/states")
    if not states_result["success"]:
        return {
            "success": False,
            "error": f"Could not fetch states: {states_result['error']}",
        }

    states_map = {s["entity_id"]: s for s in states_result["data"]}

    for entity_id in entity_list:
        if entity_id in states_map:
            state_data = states_map[entity_id]
            attributes = state_data.get("attributes", {})
            state = state_data.get("state")

            result["exists_count"] += 1  # type: ignore[operator]

            entity_result = {
                "entity_id": entity_id,
                "exists": True,
                "state": state,
                "friendly_name": attributes.get("friendly_name", ""),
                "device_class": attributes.get("device_class"),
                "unit": attributes.get("unit_of_measurement"),
            }

            if state == "unavailable":
                result["unavailable_count"] += 1  # type: ignore[operator]
                entity_result["status"] = "UNAVAILABLE"
                result["issues"].append(f"{entity_id} is unavailable")  # type: ignore[attr-defined]
            elif state == "unknown":
                entity_result["status"] = "UNKNOWN"
                result["issues"].append(f"{entity_id} state is unknown")  # type: ignore[attr-defined]
            else:
                entity_result["status"] = "OK"

            result["results"].append(entity_result)  # type: ignore[attr-defined]
        else:
            result["missing_count"] += 1  # type: ignore[operator]
            result["results"].append(  # type: ignore[attr-defined]
                {"entity_id": entity_id, "exists": False, "status": "NOT FOUND"}
            )
            result["issues"].append(f"{entity_id} not found")  # type: ignore[attr-defined]

    result["summary"] = {
        "exists": f"{result['exists_count']}/{result['total_entities']}",
        "missing": result["missing_count"],
        "unavailable": result["unavailable_count"],
        "health": "All OK"
        if result["missing_count"] == 0 and result["unavailable_count"] == 0
        else "Issues found",
    }

    return result


def _do_test_service_call(domain, service, entity_id, data, ha_url, ha_token):  # type: ignore[no-untyped-def]
    services_result = make_ha_request(ha_url, ha_token, "/api/services")

    if not services_result["success"]:
        return {
            "success": False,
            "error": f"Error fetching services: {services_result['error']}",
        }

    domain_data = None
    for d in services_result["data"]:
        if d["domain"] == domain:
            domain_data = d
            break

    if not domain_data:
        return {
            "success": False,
            "valid": False,
            "error": f"Domain '{domain}' not found",
            "available_domains": sorted([d["domain"] for d in services_result["data"]])[:20],
        }

    if service not in domain_data["services"]:
        return {
            "success": False,
            "valid": False,
            "error": f"Service '{service}' not found in domain '{domain}'",
            "available_services": sorted(list(domain_data["services"].keys())),
        }

    service_info = domain_data["services"][service]

    entity_validation = None
    if entity_id:
        entity_result = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")
        if not entity_result["success"]:
            return {
                "success": False,
                "valid": False,
                "error": f"Entity '{entity_id}' not found",
            }
        entity_validation = {
            "exists": True,
            "current_state": entity_result["data"].get("state"),
        }

    parsed_data = {}
    data_validation = {"valid": True}
    if data:
        try:
            parsed_data = json.loads(data)
            service_fields = service_info.get("fields", {})
            unknown_fields = [k for k in parsed_data.keys() if k not in service_fields]
            if unknown_fields:
                data_validation["warnings"] = f"Unknown fields: {', '.join(unknown_fields)}"  # type: ignore[assignment]
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "valid": False,
                "error": f"Invalid JSON in data: {str(e)}",
            }

    return {
        "success": True,
        "valid": True,
        "service": f"{domain}.{service}",
        "entity_id": entity_id,
        "entity_validation": entity_validation,
        "data": parsed_data,
        "data_validation": data_validation,
        "service_info": {
            "description": service_info.get("description", ""),
            "fields": {
                name: {
                    "description": field.get("description", ""),
                    "example": field.get("example"),
                    "required": field.get("required", False),
                }
                for name, field in service_info.get("fields", {}).items()
            },
        },
        "note": "DRY RUN - service was NOT executed",
    }


def _do_diagnose_entity(entity_id, ha_url, ha_token, config_path):  # type: ignore[no-untyped-def]
    result = {
        "success": True,
        "entity_id": entity_id,
        "entity_info": {},
        "current_state": {},
        "history_summary": {},
        "device_info": {},
        "area_info": {},
        "related_entities": [],
        "recent_logs": [],
        "issues": [],
        "recommendations": [],
    }

    state_result = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")
    if not state_result["success"]:
        result["success"] = False
        result["issues"].append(
            {
                "severity": "error",
                "message": f"Entity not found or unavailable: {state_result['error']}",
            }
        )
        result["recommendations"].append("Check if entity exists in entity registry")
        return result

    state_data = state_result["data"]
    attributes = state_data.get("attributes", {})
    result["current_state"] = {
        "state": state_data.get("state"),
        "friendly_name": attributes.get("friendly_name", ""),
        "device_class": attributes.get("device_class"),
        "unit_of_measurement": attributes.get("unit_of_measurement"),
        "icon": attributes.get("icon"),
        "last_changed": state_data.get("last_changed"),
        "last_updated": state_data.get("last_updated"),
        "attributes_count": len(attributes),
    }

    device_id = None
    area_id = None

    if config_path:
        try:
            registry_path = Path(config_path) / ".storage/core.entity_registry"
            if registry_path.exists():
                with open(registry_path) as f:
                    registry = json.load(f)
                    for entity in registry.get("data", {}).get("entities", []):
                        if entity.get("entity_id") == entity_id:
                            device_id = entity.get("device_id")
                            area_id = entity.get("area_id")
                            result["entity_info"] = {
                                "platform": entity.get("platform"),
                                "device_id": device_id,
                                "area_id": area_id,
                                "disabled": entity.get("disabled_by") is not None,
                                "disabled_by": entity.get("disabled_by"),
                                "hidden": entity.get("hidden_by") is not None,
                                "hidden_by": entity.get("hidden_by"),
                                "unique_id": entity.get("unique_id"),
                                "config_entry_id": entity.get("config_entry_id"),
                            }

                            if entity.get("disabled_by"):
                                result["issues"].append(
                                    {
                                        "severity": "warning",
                                        "message": f"Entity is disabled by: {entity.get('disabled_by')}",
                                    }
                                )

                            if device_id:
                                for other in registry.get("data", {}).get("entities", []):
                                    if (
                                        other.get("device_id") == device_id
                                        and other.get("entity_id") != entity_id
                                    ):
                                        result["related_entities"].append(
                                            {
                                                "entity_id": other.get("entity_id"),
                                                "platform": other.get("platform"),
                                                "disabled": other.get("disabled_by") is not None,
                                            }
                                        )
                            break
        except Exception as e:
            result["issues"].append(
                {
                    "severity": "warning",
                    "message": f"Could not read entity registry: {str(e)}",
                }
            )

        if device_id:
            try:
                device_registry_path = Path(config_path) / ".storage/core.device_registry"
                if device_registry_path.exists():
                    with open(device_registry_path) as f:
                        device_registry = json.load(f)
                        for device in device_registry.get("data", {}).get("devices", []):
                            if device.get("id") == device_id:
                                result["device_info"] = {
                                    "name": device.get("name"),
                                    "manufacturer": device.get("manufacturer"),
                                    "model": device.get("model"),
                                    "sw_version": device.get("sw_version"),
                                    "hw_version": device.get("hw_version"),
                                    "disabled": device.get("disabled_by") is not None,
                                }
                                break
            except Exception:
                pass

        if area_id:
            try:
                area_registry_path = Path(config_path) / ".storage/core.area_registry"
                if area_registry_path.exists():
                    with open(area_registry_path) as f:
                        area_registry = json.load(f)
                        for area in area_registry.get("data", {}).get("areas", []):
                            if area.get("id") == area_id:
                                result["area_info"] = {
                                    "name": area.get("name"),
                                    "aliases": area.get("aliases", []),
                                }
                                break
            except Exception:
                pass

        try:
            log_path = Path(config_path) / "home-assistant.log"
            if log_path.exists():
                with open(log_path, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-200:]
                    for line in lines:
                        if entity_id in line and ("ERROR" in line or "WARNING" in line):
                            result["recent_logs"].append(line.strip())

                if result["recent_logs"]:
                    result["issues"].append(
                        {
                            "severity": "warning",
                            "message": f"Found {len(result['recent_logs'])} error/warning logs",
                        }
                    )
                    result["recommendations"].append("Check recent_logs for details")
        except Exception:
            pass

    current_state = state_data.get("state")
    if current_state == "unavailable":
        result["issues"].append({"severity": "error", "message": "Entity is UNAVAILABLE"})
        result["recommendations"].append("Check device connection and integration status")
    elif current_state == "unknown":
        result["issues"].append({"severity": "warning", "message": "Entity state is UNKNOWN"})
        result["recommendations"].append("Entity may not have reported state yet")

    last_changed = state_data.get("last_changed")
    last_updated = state_data.get("last_updated")
    if last_changed and last_updated:
        try:
            changed_dt = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
            datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            now = datetime.now(changed_dt.tzinfo)

            time_since_change = (now - changed_dt).total_seconds()

            result["history_summary"] = {
                "last_changed": last_changed,
                "last_updated": last_updated,
                "state_stable": last_changed == last_updated,
                "seconds_since_change": int(time_since_change),
                "human_readable": f"{int(time_since_change // 3600)}h {int((time_since_change % 3600) // 60)}m ago",
            }

            if time_since_change > 86400:
                result["issues"].append(
                    {
                        "severity": "warning",
                        "message": f"Entity hasn't changed state in {int(time_since_change // 3600)} hours",
                    }
                )
                result["recommendations"].append("Check if device is still active")

        except Exception as e:
            result["history_summary"] = {
                "last_changed": last_changed,
                "last_updated": last_updated,
                "parse_error": str(e),
            }

    if not result["issues"]:
        result["issues"].append({"severity": "info", "message": "No issues detected"})
        result["recommendations"].append("Entity appears to be working correctly")

    return result


def _do_diagnose_template(entity_id, ha_url, ha_token, config_path):  # type: ignore[no-untyped-def]
    result = {
        "success": True,
        "entity_id": entity_id,
        "template_info": {},
        "syntax_validation": "unknown",
        "referenced_entities": [],
        "entity_status": {},
        "test_render": None,
        "performance": None,
        "issues": [],
        "recommendations": [],
    }

    template_code = None

    if config_path:
        try:
            entity_reg_path = Path(config_path) / ".storage/core.entity_registry"
            entries_path = Path(config_path) / ".storage/core.config_entries"

            config_entry_id = None
            if entity_reg_path.exists():
                with open(entity_reg_path) as f:
                    ent_reg = json.load(f)
                    for ent in ent_reg.get("data", {}).get("entities", []):
                        if ent.get("entity_id") == entity_id:
                            config_entry_id = ent.get("config_entry_id")
                            break

            if config_entry_id and entries_path.exists():
                with open(entries_path) as f:
                    entries = json.load(f)
                    for entry in entries.get("data", {}).get("entries", []):
                        if entry.get("entry_id") == config_entry_id:
                            options = entry.get("options", {})
                            name = entry.get("title", entity_id)
                            result["template_info"] = {
                                "name": name,
                                "entry_id": entry.get("entry_id"),
                                "template_type": options.get("template_type", "sensor"),
                                "device_class": options.get("device_class"),
                                "unit_of_measurement": options.get("unit_of_measurement"),
                            }
                            template_code = (
                                options.get("state")
                                or options.get("template")
                                or entry.get("data", {}).get("state")
                            )
                            break

            if not template_code and entries_path.exists():
                with open(entries_path) as f:
                    entries = json.load(f)
                    entity_name = entity_id.split(".")[-1]
                    import unicodedata

                    def _normalize(s):  # type: ignore[no-untyped-def]
                        return (
                            unicodedata.normalize("NFKD", s.lower())
                            .encode("ascii", "ignore")
                            .decode("ascii")
                            .replace(" ", "_")
                            .replace("-", "_")
                        )

                    norm_target = _normalize(entity_name)  # type: ignore[no-untyped-call]
                    for entry in entries.get("data", {}).get("entries", []):
                        if entry.get("domain") == "template":
                            options = entry.get("options", {})
                            name = entry.get("title", "")
                            if _normalize(name) == norm_target:  # type: ignore[no-untyped-call]
                                result["template_info"] = {
                                    "name": name,
                                    "entry_id": entry.get("entry_id"),
                                    "template_type": options.get("template_type", "sensor"),
                                    "device_class": options.get("device_class"),
                                    "unit_of_measurement": options.get("unit_of_measurement"),
                                }
                                template_code = (
                                    options.get("state")
                                    or options.get("template")
                                    or entry.get("data", {}).get("state")
                                )
                                break
        except Exception as e:
            result["issues"].append(
                {
                    "severity": "error",
                    "message": f"Could not read template config: {str(e)}",
                }
            )

    if not template_code:
        state_result = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")
        if state_result["success"]:
            attributes = state_result["data"].get("attributes", {})
            template_code = attributes.get("template")

    if not template_code:
        result["success"] = False
        result["issues"].append({"severity": "error", "message": "Template code not found"})
        result["recommendations"].append(
            "Entity may not be a template or is defined in YAML configuration"
        )
        return result

    entity_pattern = r"\b(sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|timer|counter|number|select|button)\.\w+\b"

    full_entities = []
    words = str(template_code).split()
    for word in words:
        matches = re.findall(entity_pattern, word)
        if matches:
            entity = re.search(entity_pattern, word)
            if entity:
                full_entities.append(entity.group(0))

    result["referenced_entities"] = list(set(full_entities))

    for ref_entity in result["referenced_entities"]:
        state_result = make_ha_request(ha_url, ha_token, f"/api/states/{ref_entity}")
        if state_result["success"]:
            state = state_result["data"].get("state")
            result["entity_status"][ref_entity] = {"state": state, "exists": True}
            if state in ["unavailable", "unknown"]:
                result["issues"].append(
                    {
                        "severity": "warning",
                        "message": f"Referenced entity {ref_entity} is {state}",
                    }
                )
        else:
            result["entity_status"][ref_entity] = {
                "state": "NOT_FOUND",
                "exists": False,
            }
            result["issues"].append(
                {
                    "severity": "error",
                    "message": f"Referenced entity {ref_entity} not found",
                }
            )

    start_time = time.time()
    render_result = make_ha_request(
        ha_url,
        ha_token,
        "/api/template",
        method="POST",
        data={"template": template_code},
    )
    render_time = time.time() - start_time

    if render_result["success"]:
        result["syntax_validation"] = "ok"
        result["test_render"] = render_result["data"]
        result["performance"] = {
            "render_time": f"{render_time:.3f}s",
            "is_slow": render_time > 0.5,
        }

        if render_time > 0.5:
            result["issues"].append(
                {
                    "severity": "warning",
                    "message": f"Slow template rendering: {render_time:.3f}s",
                }
            )
            result["recommendations"].append(
                "Consider optimizing template or caching values in variables"
            )
        elif render_time > 1.0:
            result["issues"].append(
                {
                    "severity": "error",
                    "message": f"Very slow template rendering: {render_time:.3f}s",
                }
            )
            result["recommendations"].append(
                "Template is too complex - consider splitting or using automation"
            )
    else:
        result["syntax_validation"] = "error"
        result["issues"].append(
            {
                "severity": "error",
                "message": f"Template syntax error: {render_result['error']}",
            }
        )
        result["recommendations"].append("Fix template syntax before using")

    if len(result["referenced_entities"]) > 10:
        result["recommendations"].append(
            f"Template references {len(result['referenced_entities'])} entities - consider simplifying"
        )

    if not result["issues"]:
        result["recommendations"].append("Template appears to be working correctly")
    else:
        if any(
            "unavailable" in str(issue) or "unknown" in str(issue) for issue in result["issues"]
        ):
            result["recommendations"].append(
                "Add availability template to handle missing entities gracefully"
            )
        if any("NOT_FOUND" in str(status) for status in result["entity_status"].values()):
            result["recommendations"].append("Remove or fix references to non-existent entities")

    return result


def _do_diagnose_energy_setup(ha_url, ha_token, config_path):  # type: ignore[no-untyped-def]
    result = {
        "success": True,
        "tariff": "unknown",
        "peak_hours_config": {},
        "energy_sensors": [],
        "power_sensors": [],
        "price_sensors": [],
        "missing_sensors": [],
        "automations": [],
        "automations_count": 0,
        "notification_setup": "unknown",
        "price_tracking": {},
        "issues": [],
        "recommendations": [],
    }

    states_result = make_ha_request(ha_url, ha_token, "/api/states")
    if states_result["success"]:
        for entity in states_result["data"]:
            entity_id = entity.get("entity_id", "")
            attributes = entity.get("attributes", {})
            state = entity.get("state")

            if entity_id.startswith("sensor."):
                device_class = attributes.get("device_class")
                unit = attributes.get("unit_of_measurement", "")

                if device_class == "energy" or "kWh" in unit:
                    result["energy_sensors"].append(  # type: ignore[attr-defined]
                        {
                            "entity_id": entity_id,
                            "state": state,
                            "unit": unit,
                            "friendly_name": attributes.get("friendly_name", ""),
                        }
                    )

                if device_class == "power" or "W" in unit:
                    result["power_sensors"].append(  # type: ignore[attr-defined]
                        {"entity_id": entity_id, "state": state, "unit": unit}
                    )

                if (
                    "price" in entity_id.lower()
                    or "cena" in entity_id.lower()
                    or "koszt" in entity_id.lower()
                ):
                    result["price_sensors"].append(  # type: ignore[attr-defined]
                        {"entity_id": entity_id, "state": state, "unit": unit}
                    )

            if any(
                keyword in entity_id.lower() for keyword in ["g12", "peak", "tariff", "pricing"]
            ):
                result["tariff"] = "Dual-zone tariff detected"
                result["peak_hours_config"][entity_id] = {  # type: ignore[index]
                    "state": state,
                    "friendly_name": attributes.get("friendly_name", ""),
                }

    if config_path:
        try:
            import yaml

            auto_path = Path(config_path) / "automations.yaml"
            if auto_path.exists():
                with open(auto_path, encoding="utf-8") as f:
                    automations = yaml.safe_load(f) or []

                    for auto in automations:
                        alias = auto.get("alias", "").lower()
                        description = auto.get("description", "").lower()

                        if any(
                            keyword in alias or keyword in description
                            for keyword in [
                                "energy",
                                "power",
                                "g12",
                                "tariff",
                                "peak",
                                "pricing",
                                "price",
                                "cena",
                            ]
                        ):
                            result["automations"].append(  # type: ignore[attr-defined]
                                {
                                    "id": auto.get("id"),
                                    "alias": auto.get("alias"),
                                    "description": auto.get("description", ""),
                                }
                            )
                            result["automations_count"] += 1  # type: ignore[operator]
        except Exception as e:
            result["issues"].append(  # type: ignore[attr-defined]
                {
                    "severity": "warning",
                    "message": f"Could not read automations: {str(e)}",
                }
            )

    if result["automations_count"] > 0:  # type: ignore[operator]
        result["notification_setup"] = "configured"
    else:
        result["notification_setup"] = "missing"
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "message": "No energy automations - consider adding high price notifications",
            }
        )

    if result["price_sensors"]:
        result["price_tracking"]["status"] = "configured"  # type: ignore[index]
        result["price_tracking"]["sensors_count"] = len(result["price_sensors"])  # type: ignore[arg-type, index]
    else:
        result["price_tracking"]["status"] = "missing"  # type: ignore[index]
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "high",
                "message": "No energy price sensors - add a sensor to track current prices",
            }
        )

    common_energy_devices = {
        "washing_machine": "Pralka",
        "dishwasher": "Zmywarka",
        "dryer": "Suszarka",
        "oven": "Piekarnik",
        "water_heater": "Bojler",
        "ev_charger": "EV Charger",
        "solar": "Solar Panels",
        "heat_pump": "Heat Pump",
    }

    for device_key, device_name in common_energy_devices.items():
        found = any(
            device_key in s["entity_id"].lower()
            for s in result["energy_sensors"] + result["power_sensors"]  # type: ignore[operator]
        )
        if not found:
            result["missing_sensors"].append({"device": device_name, "key": device_key})  # type: ignore[attr-defined]

    if result["tariff"] == "Dual-zone tariff detected":
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "info",
                "message": "Dual-zone tariff detected - remember peak hours: 06-13 and 15-22 (workdays)",
            }
        )
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "message": "Consider automations for large consumers outside peak hours (savings ~250 currency units/month)",
            }
        )
    else:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "high",
                "message": "Time-of-use tariff configuration not detected - add binary_sensor to track peak hours",
            }
        )

    if len(result["energy_sensors"]) < 3:  # type: ignore[arg-type]
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "message": f"Found only {len(result['energy_sensors'])} energy sensors - consider adding more for better monitoring",  # type: ignore[arg-type]
            }
        )

    if result["missing_sensors"]:
        top_missing = [s["device"] for s in result["missing_sensors"][:3]]  # type: ignore[index]
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "low",
                "message": f"Consider adding monitoring for: {', '.join(top_missing)}",
            }
        )

    if result["automations_count"] == 0:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "high",
                "message": "No energy automations - add notifications about high prices and peak hours",
            }
        )
    elif result["automations_count"] < 3:  # type: ignore[operator]
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "message": f"Only {result['automations_count']} energy automations - consider adding more (e.g. automatic device shutdown during peak)",
            }
        )

    result["statistics"] = {
        "total_energy_sensors": len(result["energy_sensors"]),  # type: ignore[arg-type]
        "total_power_sensors": len(result["power_sensors"]),  # type: ignore[arg-type]
        "total_price_sensors": len(result["price_sensors"]),  # type: ignore[arg-type]
        "total_automations": result["automations_count"],
        "missing_sensors_count": len(result["missing_sensors"]),  # type: ignore[arg-type]
    }

    priority_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    result["recommendations"].sort(key=lambda x: priority_order.get(x.get("priority", "info"), 3))  # type: ignore[attr-defined]

    return result


# =============================================================================
# MCP TOOL REGISTRATION
# =============================================================================


def register_dev_tools(mcp, ha_url: str, ha_token: str, config_path: str | None = None) -> None:  # type: ignore[no-untyped-def]
    """
    Registers Home Assistant developer tools.

    Args:
        mcp: FastMCP instance.
        ha_url: Home Assistant API URL.
        ha_token: Authorization token.
        config_path: Path to HA configuration directory (optional, required for diagnostics).
    """

    @mcp.tool()
    def test_template(template: str) -> str:
        """[READ] Tests a Jinja2 template in Home Assistant (like Developer Tools > Template).
        Returns rendering result or error.

        Warning: For multiple templates use test_templates_batch() - saves ~80% tokens.

        Args:
            template: Jinja2 template to test (e.g. "{{ states('sensor.temperature') }}")

        Examples:
            test_template("{{ states('sensor.temperature') }}")
            test_template("{{ state_attr('light.living_room', 'brightness') }}")
            test_template("{{ now().hour }}")
        """
        try:
            result = _do_test_template(template, None, None, ha_url, ha_token)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("test_template failed")
            return _error_response(str(exc))

    @mcp.tool()
    def test_templates_batch(templates: str) -> str:
        """[READ] BATCH TESTING - Tests multiple templates simultaneously.

        ~80% token savings when testing multiple templates.
        Instead of: test_template() x N

        Args:
            templates: JSON array of templates or dict with names

        Examples:
            test_templates_batch('["{{ now().hour }}", "{{ states('sensor.temp') }}"]')
            test_templates_batch('{"hour": "{{ now().hour }}", "temp": "{{ states('sensor.temp') }}"}')

        Returns:
            JSON with results of all tests, statistics, and performance comparison
        """
        try:
            result = _do_test_templates_batch(templates, ha_url, ha_token)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("test_templates_batch failed")
            return _error_response(str(exc))

    @mcp.tool()
    def eval_templates_batch(templates: str, mock_variables: str | None = None) -> str:
        """[READ] Evaluate multiple Jinja2 templates at once, optionally injecting mock variables for deterministic testing.

        Args:
            templates: JSON array of template strings or dict of name->template mappings.
            mock_variables: Optional JSON object of variable name->value pairs to inject into all templates via {% set %} statements.

        Returns:
            JSON with total/successful/failed counts and per-template results dict.

        Example:
            eval_templates_batch('["{{ 2 + 2 }}", "{{ states(\"sensor.temp\") }}"]')
            eval_templates_batch('{"calc": "{{ 2 + 2 }}", "temp": "{{ states(\"sensor.temp\") }}"}')
            eval_templates_batch('{"greeting": "{{ name }}"}', '{"name": "World"}')
        """
        try:
            result = _do_eval_templates_batch(templates, mock_variables, ha_url, ha_token)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("eval_templates_batch failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_template_performance(template: str, iterations: int = 5) -> str:
        """[READ] BENCHMARK - Measures template performance through multiple executions.

        Args:
            template: template code to benchmark.
            iterations: Number of iterations (default: 5, max: 20).

        Returns:
            JSON with statistics (min/max/avg time) and complexity assessment.
        """
        try:
            result = _do_get_template_performance(template, iterations, ha_url, ha_token)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("get_template_performance failed")
            return _error_response(str(exc))

    @mcp.tool()
    def validate_automation_trigger(trigger_config: str) -> str:
        """[READ] Validates automation trigger configuration.

        Args:
            trigger_config: YAML trigger configuration as a string

        Example:
            validate_automation_trigger('''
            - platform: state
              entity_id: sensor.temperature
              above: 25
            ''')
        """
        try:
            result = _do_validate_automation_trigger(trigger_config, ha_url, ha_token)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("validate_automation_trigger failed")
            return _error_response(str(exc))

    @mcp.tool()
    def test_condition(condition_template: str, context: str | None = None) -> str:
        """[READ] Tests a condition with optional context.

        Args:
            condition_template: Condition template to test
            context: Optional context (e.g. variable values)

        Example:
            test_condition("{{ states('sensor.temperature') | float > 25 }}")
            test_condition("{{ trigger.to_state.state == 'on' }}", "trigger.to_state.state = 'on'")
        """
        try:
            result = _do_test_condition(condition_template, context, ha_url, ha_token)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("test_condition failed")
            return _error_response(str(exc))

    @mcp.tool()
    def check_entity_exists(entity_id: str) -> str:
        """[READ] Quickly checks if entity exists in the system.

        Warning: For multiple entities use check_entities_batch() - saves ~85% tokens.

        Args:
            entity_id: Entity id to check
        """
        try:
            result = _do_check_entity_exists(entity_id, ha_url, ha_token)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("check_entity_exists failed")
            return _error_response(str(exc))

    @mcp.tool()
    def check_entities_batch(entity_ids: str) -> str:
        """[READ] BATCH CHECKING - Checks multiple entities simultaneously.

        ~85% token savings when checking multiple entities.
        Instead of: check_entity_exists() x N

        Args:
            entity_ids: Comma-separated list of entity ids

        Example:
            check_entities_batch("sensor.temp1,sensor.temp2,light.living_room")

        Returns:
            JSON with:
            - summary: statistics (exists, missing, unavailable)
            - results: details of each entity
            - issues: list of problems
        """
        try:
            result = _do_check_entities_batch(entity_ids, ha_url, ha_token)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("check_entities_batch failed")
            return _error_response(str(exc))

    @mcp.tool()
    def test_service_call(
        domain: str, service: str, entity_id: str | None = None, data: str | None = None
    ) -> str:
        """[READ] Validates whether a service call is correct (WITHOUT EXECUTING!).
        Only checks if the service exists and parameters are correct.

        Args:
            domain: Service domain (e.g. 'light', 'switch')
            service: Service name (e.g. 'turn_on', 'toggle')
            entity_id: Optional entity id
            data: Optional data as JSON string

        Example:
            test_service_call('light', 'turn_on', 'light.living_room', '{"brightness": 255}')
        """
        try:
            result = _do_test_service_call(domain, service, entity_id, data, ha_url, ha_token)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("test_service_call failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_entity(entity_id: str) -> str:
        """[READ] CONTEXTUALIZED DIAGNOSTICS - Comprehensive entity diagnostics.

        ~80% token savings when analyzing entities.
        Instead of: get_entity_state() + get_entity_details() + get_device_registry() + search logs

        Args:
            entity_id: Entity id to diagnose

        Returns:
            JSON with:
            - entity_info: basic entity information
            - current_state: current state and attributes
            - history_summary: history summary (last_changed, last_updated)
            - related_entities: related entities (same device/area)
            - issues: found issues
            - recommendations: fix suggestions
        """
        try:
            result = _do_diagnose_entity(entity_id, ha_url, ha_token, config_path)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_entity failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_template(entity_id: str) -> str:
        """[READ] CONTEXTUALIZED DIAGNOSTICS - Comprehensive template sensor/helper diagnostics.

        Args:
            entity_id: id template entity (e.g. "sensor.my_template")

        Returns:
            JSON with:
            - template_info: basic info
            - syntax_validation: ok/error
            - referenced_entities: list of used entities
            - entity_status: status of each used entity
            - test_render: Render test
            - performance: rendering time
            - issues: found issues
            - recommendations: fix suggestions
        """
        try:
            result = _do_diagnose_template(entity_id, ha_url, ha_token, config_path)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_template failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_energy_setup() -> str:
        """[READ] CONTEXTUALIZED DIAGNOSTICS - Energy configuration diagnostics (especially for time-of-use tariff).

        Returns:
            JSON with:
            - tariff: detected tariff
            - peak_hours_config: peak hours configuration status
            - energy_sensors: list of energy sensors
            - missing_sensors: missing sensors
            - automations_count: number of energy automations
            - notification_setup: notification status
            - price_tracking: price tracking status
            - recommendations: optimization suggestions
        """
        try:
            result = _do_diagnose_energy_setup(ha_url, ha_token, config_path)  # type: ignore[no-untyped-call]
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_energy_setup failed")
            return _error_response(str(exc))

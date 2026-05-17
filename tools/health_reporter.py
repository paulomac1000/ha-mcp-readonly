"""
Health Reporter - Token-Optimized system health monitoring for Home Assistant
REUSES existing MCP functions instead of duplicating code.
Designed for low-resource systems - no LLM on device, data prepared for external analysis.

READ-ONLY: Returns JSON reports. Does NOT write to Home Assistant.
"""

import logging
import os
import re
import traceback
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from tools.utils import _error_response, _success_response, make_ha_request, tail_log_file

_logger = logging.getLogger(__name__)


def collect_system_metrics(ha_url: str, ha_token: str | None, config_path: str) -> dict[str, Any]:
    """Collect basic system metrics from HA API (lightweight)"""
    try:
        metrics: dict[str, Any] = {"timestamp": datetime.now().isoformat()}

        try:
            response = make_ha_request(ha_url, ha_token, "/api/config")

            if response["success"]:
                config = response["data"]
                metrics["core_version"] = config.get("version", "unknown")
                metrics["location_name"] = config.get("location_name", "unknown")

                components = config.get("components", [])
                if isinstance(components, list):
                    metrics["integration_count"] = len(components)
                    metrics["integrations_sample"] = sorted(components)[:30]
                else:
                    metrics["integration_count"] = 0
                    metrics["integrations_sample"] = []
            else:
                metrics["core_version"] = "unknown"
                metrics["integration_count"] = 0
                metrics["integrations_sample"] = []
                metrics["config_error"] = response.get("error", "Unknown error")

        except Exception as e:
            metrics["core_version"] = "unknown"
            metrics["integration_count"] = 0
            metrics["config_error"] = str(e)[:100]

        try:
            supervisor = make_ha_request(ha_url, ha_token, "/api/hassio/supervisor/info")
            if supervisor["success"] and isinstance(supervisor["data"], dict):
                metrics["supervisor_version"] = (
                    supervisor["data"].get("data", {}).get("version", "not_available")
                )
        except Exception:
            metrics["supervisor_version"] = "not_available"

        try:
            core = make_ha_request(ha_url, ha_token, "/api/hassio/core/info")
            if core["success"] and isinstance(core["data"], dict):
                metrics["uptime_seconds"] = core["data"].get("data", {}).get("uptime", 0)
        except Exception:
            metrics["uptime_seconds"] = 0

        return metrics

    except Exception as e:
        return {"error": str(e)[:200], "timestamp": datetime.now().isoformat()}


def collect_log_summary_optimized(
    ha_url: str, ha_token: str | None, config_path: str, hours: int = 24
) -> dict[str, Any]:
    """Collect and group log errors - OPTIMIZED version"""
    try:
        log_path = os.path.join(config_path, "home-assistant.log")
        if not os.path.exists(log_path):
            return {"error": "log file not found"}

        uptime_seconds = 0
        try:
            core = make_ha_request(ha_url, ha_token, "/api/hassio/core/info")
            if core["success"] and isinstance(core["data"], dict):
                uptime_seconds = core["data"].get("data", {}).get("uptime", 0)
        except Exception:
            pass

        if uptime_seconds > 0:
            cutoff = datetime.now() - timedelta(seconds=uptime_seconds + 60)
            hours = min(hours, int(uptime_seconds / 3600) + 1)
        else:
            cutoff = datetime.now() - timedelta(hours=hours)

        errors_raw = []
        warnings_raw = []

        lines = tail_log_file(log_path, lines=10000)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if match:
                try:
                    ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                    if ts < cutoff:
                        continue
                except Exception:
                    pass

            if "ERROR" in line:
                errors_raw.append(line)
            elif "WARNING" in line:
                warnings_raw.append(line)

        def normalize_message(msg: str) -> str:
            msg = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}", "TIMESTAMP", msg)
            msg = re.sub(r"\d+\.\d+\.\d+\.\d+", "IP", msg)
            msg = re.sub(r"[a-f0-9]{8,}", "ID", msg)
            msg = re.sub(r"\d+", "N", msg)
            return msg[:200]

        error_groups: Counter[str] = Counter()
        warning_groups: Counter[str] = Counter()
        error_examples = {}

        for err in errors_raw:
            normalized = normalize_message(err)
            error_groups[normalized] += 1
            if normalized not in error_examples:
                error_examples[normalized] = err[:300]

        for warn in warnings_raw:
            normalized = normalize_message(warn)
            warning_groups[normalized] += 1

        component_errors: Counter[str] = Counter()
        for err in errors_raw:
            match = re.search(r"\[([^\]]+)\]", err)
            if match:
                comp = match.group(1)
                if "components." in comp:
                    comp = comp.split("components.")[1].split(".")[0]
                component_errors[comp] += 1

        return {
            "period_hours": hours,
            "since_restart": uptime_seconds > 0,
            "total_error_count": len(errors_raw),
            "total_warning_count": len(warnings_raw),
            "unique_error_patterns": len(error_groups),
            "top_error_groups": [
                {
                    "pattern": pattern,
                    "count": count,
                    "example": error_examples.get(pattern, "")[:200],
                }
                for pattern, count in error_groups.most_common(10)
            ],
            "top_warning_groups": [
                {"pattern": pattern, "count": count}
                for pattern, count in warning_groups.most_common(5)
            ],
            "component_errors": dict(component_errors.most_common(10)),
        }
    except Exception as e:
        return {"error": str(e)[:200], "traceback": traceback.format_exc()[:300]}


def collect_entity_health(ha_url: str, ha_token: str | None) -> dict[str, Any]:
    """Analyze entity states - OPTIMIZED version"""
    try:
        response = make_ha_request(ha_url, ha_token, "/api/states")

        if not response["success"]:
            return {"error": f"API error: {response.get('error')}"}

        states = response["data"]

        total = len(states)
        unavailable = []
        unknown = []

        unavailable_by_domain: Counter[str] = Counter()
        unknown_by_domain: Counter[str] = Counter()

        for entity in states:
            if not isinstance(entity, dict):
                continue

            state = entity.get("state", "")
            entity_id = entity.get("entity_id", "")

            if state == "unavailable":
                unavailable.append(entity_id)
                domain = entity_id.split(".")[0]
                unavailable_by_domain[domain] += 1
            elif state == "unknown":
                unknown.append(entity_id)
                domain = entity_id.split(".")[0]
                unknown_by_domain[domain] += 1

        return {
            "total_entities": total,
            "unavailable_count": len(unavailable),
            "unknown_count": len(unknown),
            "unavailable_by_domain": dict(unavailable_by_domain.most_common(10)),
            "unknown_by_domain": dict(unknown_by_domain.most_common(10)),
            "unavailable_sample": unavailable[:20],
            "unknown_sample": unknown[:20],
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def collect_automation_health(ha_url: str, ha_token: str | None) -> dict[str, Any]:
    """Check automation states - OPTIMIZED version"""
    try:
        response = make_ha_request(ha_url, ha_token, "/api/states")

        if not response["success"]:
            return {"error": f"API error: {response.get('error')}"}

        states = response["data"]

        automations = [
            s
            for s in states
            if isinstance(s, dict) and s.get("entity_id", "").startswith("automation.")
        ]

        total = len(automations)
        disabled = []
        never_triggered = []

        for auto in automations:
            entity_id = auto.get("entity_id", "")
            state = auto.get("state", "")
            attrs = auto.get("attributes", {})
            last_triggered = attrs.get("last_triggered") if isinstance(attrs, dict) else None

            if state == "off":
                disabled.append(entity_id)

            if not last_triggered:
                never_triggered.append(entity_id)

        return {
            "total_automations": total,
            "disabled_count": len(disabled),
            "never_triggered_count": len(never_triggered),
            "disabled_sample": disabled[:10],
            "never_triggered_sample": never_triggered[:10],
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def calculate_health_score(data: dict[str, Any]) -> dict[str, Any]:
    """Calculate overall health score (0-100) based on collected metrics"""
    score = 100
    issues = []

    entity_health = data.get("entity_health", {})
    unavailable = entity_health.get("unavailable_count", 0)
    unknown = entity_health.get("unknown_count", 0)
    total_entities = entity_health.get("total_entities", 1)

    unavailable_pct = (unavailable / total_entities) * 100 if total_entities > 0 else 0
    if unavailable_pct > 1:
        penalty = min(30, int(unavailable_pct * 2))
        if unavailable_pct >= 80:
            penalty = min(40, penalty + 10)
        score -= penalty
        issues.append(f"{unavailable} unavailable entities ({unavailable_pct:.1f}%, -{penalty})")

    unknown_pct = (unknown / total_entities) * 100 if total_entities > 0 else 0
    if unknown_pct > 1:
        penalty = min(15, int(unknown_pct * 1.5))
        score -= penalty
        issues.append(f"{unknown} unknown entities ({unknown_pct:.1f}%, -{penalty})")

    log_summary = data.get("log_summary", {})
    error_count = log_summary.get("total_error_count", 0)
    unique_patterns = log_summary.get("unique_error_patterns", 0)

    if unique_patterns > 5:
        penalty = min(25, unique_patterns * 2)
        score -= penalty
        issues.append(f"{unique_patterns} unique error patterns, {error_count} total (-{penalty})")
    elif error_count > 10:
        penalty = min(20, int(error_count * 0.5))
        score -= penalty
        issues.append(f"{error_count} errors in logs (-{penalty})")

    auto_health = data.get("automation_health", {})
    disabled = auto_health.get("disabled_count", 0)

    if disabled > 5:
        penalty = min(10, disabled)
        score -= penalty
        issues.append(f"{disabled} disabled automations (-{penalty})")

    score = max(0, min(100, score))

    if score >= 90:
        status = "excellent"
    elif score >= 75:
        status = "good"
    elif score >= 50:
        status = "fair"
    elif score >= 25:
        status = "poor"
    else:
        status = "critical"

    return {
        "score": round(score, 1),
        "status": status,
        "issues": issues,
    }


def prepare_report(data: dict[str, Any]) -> dict[str, Any]:
    """Prepare final report with cleaned, aggregated data"""
    health_score = calculate_health_score(data)

    report = {
        "version": "1.3",
        "generated_at": datetime.now().isoformat(),
        "health_score": health_score,
        "system_metrics": data.get("system_metrics", {}),
        "log_summary": data.get("log_summary", {}),
        "entity_health": data.get("entity_health", {}),
        "automation_health": data.get("automation_health", {}),
    }

    return report


def run_once(ha_url: str, ha_token: str | None, config_path: str) -> dict[str, Any]:
    """Main entry point - collect and return health report as JSON (read-only)."""
    _logger.info("Starting health report generation")

    try:
        data = {
            "system_metrics": collect_system_metrics(ha_url, ha_token, config_path),
            "log_summary": collect_log_summary_optimized(ha_url, ha_token, config_path, hours=24),
            "entity_health": collect_entity_health(ha_url, ha_token),
            "automation_health": collect_automation_health(ha_url, ha_token),
        }

        report = prepare_report(data)
        _logger.info("Report generated successfully")
        return report

    except Exception as e:
        _logger.error(f"Fatal error: {e}")
        traceback.print_exc()

        return {
            "version": "1.3",
            "generated_at": datetime.now().isoformat(),
            "health_score": {
                "score": 0,
                "status": "error",
                "issues": [f"Health reporter failed: {str(e)[:100]}"],
            },
            "error": str(e)[:200],
            "traceback": traceback.format_exc()[:500],
        }


def _do_trigger_health_report(ha_url: str, ha_token: str | None, config_path: str) -> str:
    """Generate system health report."""
    report = run_once(ha_url, ha_token, config_path)
    return _success_response(report)


def register_health_reporter_tools(
    mcp: Any, ha_url: str, ha_token: str | None, config_path: str
) -> None:
    """Register health reporter as MCP tool for manual triggering"""

    @mcp.tool()
    def trigger_health_report() -> str:
        """[READ] Generate system health report (read-only). Returns JSON with metrics, logs, entity and automation health."""
        try:
            return _do_trigger_health_report(ha_url, ha_token, config_path)
        except Exception as e:
            return _error_response(str(e))

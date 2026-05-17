"""
Shared Utilities for MCP Tools

Provides common functionality used across all tools:
- HTTP client for Home Assistant API with retry logic
- Registry file loading with caching and blocklist
- Log sanitization (security -- prevents token/credential leaks)
- Common helper functions

CHANGES vs original:
- Added BLOCKED_REGISTRIES frozenset (security)
- Added sanitize_log_line() (security)
- Added get_registry_cache_stats() (observability)
- Enhanced load_registry() with blocklist + hit/miss counters
- Added invalidate_registry_cache() for manual invalidation
- All existing public functions preserved -- zero breaking changes
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

_REGISTRY_CACHE: dict[str, tuple[Any, float]] = {}
_REGISTRY_TTL = 300  # seconds -- 5 minutes

_REGISTRY_CACHE_STATS: dict[str, int] = {"hits": 0, "misses": 0, "blocked": 0}
_CACHE_LOCK = threading.Lock()

# =============================================================================
# SECURITY: BLOCKED REGISTRIES
# =============================================================================

BLOCKED_REGISTRIES = frozenset(
    {
        "auth",
        "auth_provider.homeassistant",
        "onboarding",
    }
)
"""Registry names that must never be loaded or returned to AI agents.
These contain credentials, password hashes, and auth tokens."""

# =============================================================================
# SECURITY: LOG SANITIZATION
# =============================================================================

_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # JWT must come before Bearer (JWT is a superset pattern)
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"),
        "[JWT_REDACTED]",
    ),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|pwd)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (
        re.compile(r"(?i)\b(token|access_token|refresh_token)\s*[=:]\s*\S+"),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)\b(api_key|apikey|api-key)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\b(secret|client_secret)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[IP_REDACTED]"),
]


def sanitize_log_line(line: str) -> str:
    """
    Remove sensitive data from a log line before returning to AI agents.

    Redacts: JWTs, Bearer tokens, passwords, tokens, API keys, secrets,
    and IPv4 addresses.

    This function is designed to be called at the output boundary -- just
    before log content is serialised into a tool response.
    """
    for pattern, replacement in _SENSITIVE_PATTERNS:
        line = pattern.sub(replacement, line)
    return line


def get_registry_cache_stats() -> dict[str, Any]:
    """
    Return cache hit / miss / blocked statistics for monitoring.

    Useful for verifying the 70%+ hit rate target in production.
    """
    with _CACHE_LOCK:
        total = _REGISTRY_CACHE_STATS["hits"] + _REGISTRY_CACHE_STATS["misses"]
        hit_rate = (_REGISTRY_CACHE_STATS["hits"] / total * 100) if total > 0 else 0.0
        return {
            "hits": _REGISTRY_CACHE_STATS["hits"],
            "misses": _REGISTRY_CACHE_STATS["misses"],
            "blocked": _REGISTRY_CACHE_STATS["blocked"],
            "total": total,
            "hit_rate_percent": round(hit_rate, 1),
            "cached_keys": len(_REGISTRY_CACHE),
        }


# =============================================================================
# HTTP CLIENT
# =============================================================================


def make_ha_request(
    ha_url: str,
    ha_token: str,
    endpoint: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    timeout: int = 10,
    retries: int = 3,
    backoff: float = 1.0,
) -> dict[str, Any]:
    """
    Execute HTTP request to Home Assistant API with exponential-backoff retry.

    Returns ``{"success": True, "data": ...}`` on success,
    ``{"success": False, "error": "..."}`` on failure.
    """
    url = f"{ha_url}{endpoint}"
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }

    last_error: str | None = None
    last_code = "HTTP_ERROR"
    last_retryable = True

    for attempt in range(retries):
        try:
            if method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
            else:
                response = requests.get(url, headers=headers, timeout=timeout)

            response.raise_for_status()

            try:
                return {"success": True, "data": response.json()}
            except ValueError:
                return {"success": True, "data": response.text}

        except requests.exceptions.RequestException as exc:
            last_error = str(exc)
            if isinstance(exc, requests.exceptions.Timeout):
                last_code = "TIMEOUT"
                last_retryable = True
            elif isinstance(exc, requests.exceptions.HTTPError):
                last_code = "HTTP_ERROR"
                status = getattr(getattr(exc, "response", None), "status_code", None)
                last_retryable = status is None or (isinstance(status, int) and status >= 500)
            else:
                last_code = "HTTP_ERROR"
                last_retryable = True
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))

    # ``error`` stays a string for backward compatibility; ``error_code`` and
    # ``retryable`` are the structured (extended error contract) siblings.
    return {
        "success": False,
        "error": last_error,
        "error_code": last_code,
        "retryable": last_retryable,
    }


# =============================================================================
# REGISTRY LOADING WITH CACHE + BLOCKLIST
# =============================================================================


def load_registry(
    registry_name: str,
    config_path: str,
    use_cache: bool = True,
) -> dict[str, Any]:
    """
    Load a registry file from ``.storage`` with caching and blocklist.

    Blocked registries (``auth``, ``auth_provider.homeassistatet``,
    ``onboarding``) silently return ``{}`` to prevent credential leaks.
    """
    if registry_name in BLOCKED_REGISTRIES:
        with _CACHE_LOCK:
            _REGISTRY_CACHE_STATS["blocked"] += 1
        return {}

    cache_key = f"{config_path}/{registry_name}"
    now = time.time()

    with _CACHE_LOCK:
        if use_cache and cache_key in _REGISTRY_CACHE:
            data, timestamp = _REGISTRY_CACHE[cache_key]
            if now - timestamp < _REGISTRY_TTL:
                _REGISTRY_CACHE_STATS["hits"] += 1
                return data  # type: ignore[no-any-return]

        _REGISTRY_CACHE_STATS["misses"] += 1

    try:
        path = Path(config_path) / ".storage" / registry_name
        if not path.exists():
            return {}

        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        with _CACHE_LOCK:
            _REGISTRY_CACHE[cache_key] = (data, now)
        return data  # type: ignore[no-any-return]

    except (OSError, json.JSONDecodeError, KeyError) as exc:
        logger.warning(f"Error loading registry {registry_name}: {exc}")
        with _CACHE_LOCK:
            _REGISTRY_CACHE.pop(cache_key, None)
        return {}


def invalidate_registry_cache(
    registry_name: str | None = None,
    config_path: str | None = None,
) -> None:
    """
    Invalidate registry cache entries.

    Call with no arguments to flush all entries.
    """
    global _REGISTRY_CACHE

    with _CACHE_LOCK:
        if registry_name is None and config_path is None:
            _REGISTRY_CACHE.clear()
        else:
            keys_to_remove = []
            for key in list(_REGISTRY_CACHE):
                key_str = str(key)
                if registry_name and registry_name in key_str:
                    keys_to_remove.append(key)
                elif config_path and key_str.startswith(f"{config_path}/"):
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del _REGISTRY_CACHE[key]


def get_registry_entities(config_path: str) -> list[dict[str, Any]]:
    """Shorthand: get entities from entity registry."""
    return load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])  # type: ignore[no-any-return]


def get_registry_devices(config_path: str) -> list[dict[str, Any]]:
    """Shorthand: get devices from device registry."""
    return load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])  # type: ignore[no-any-return]


def get_registry_areas(config_path: str) -> list[dict[str, Any]]:
    """Shorthand: get areas from area registry."""
    return load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])  # type: ignore[no-any-return]


def get_registry_config_entries(config_path: str) -> list[dict[str, Any]]:
    """Shorthand: get config entries from registry."""
    return load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])  # type: ignore[no-any-return]


# =============================================================================
# LOG FILE UTILITIES
# =============================================================================


def tail_log_file(
    log_path: str,
    lines: int = 1000,
    encoding: str = "utf-8",
) -> list[str]:
    """Read last *lines* from log file using ``tail``."""
    if not os.path.exists(log_path):
        return []

    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), log_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.splitlines()
    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        logger.warning(f"Error reading log file: {exc}")
        return []


# =============================================================================
# COMMON HELPERS
# =============================================================================


def get_best_name(item: dict[str, Any], item_type: str = "entity") -> str:
    """
    Best available name for an entity or device.

    Priority: ``name_by_user > name > original_name > entity_id/id``.
    """
    if item_type == "device":
        return item.get("name_by_user") or item.get("name") or "Unknown Device"
    return item.get("name") or item.get("original_name") or item.get("entity_id", "Unknown")  # type: ignore[no-any-return]


def resolve_area_id(entity: dict[str, Any], device_map: dict[str, dict[str, Any]]) -> str | None:
    """Resolve area: entity area → device area → ``None``."""
    if entity.get("area_id"):
        return entity["area_id"]  # type: ignore[no-any-return]
    device_id = entity.get("device_id")
    if device_id and device_id in device_map:
        return device_map[device_id].get("area_id")
    return None


def sanitize_for_json(obj: Any) -> Any:
    """Deep-sanitize a dict/list, replacing values whose keys look sensitive."""
    sensitive_keys = {
        "password",
        "token",
        "api_key",
        "secret",
        "client_id",
        "client_secret",
        "ssid",
        "key",
    }
    if isinstance(obj, dict):
        return {
            k: "***REDACTED***"
            if any(s in k.lower() for s in sensitive_keys)
            else sanitize_for_json(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    return obj


def _success_response(
    data: dict[str, Any] | None = None,
    _meta: dict[str, object] | None = None,
) -> str:
    """Format a successful tool response. All tools MUST use this.

    Args:
        data: Result data dict or any serializable value.
        _meta: Optional metadata envelope (build_meta output).
    """
    response: dict[str, Any] = {"success": True}

    if data is not None:
        sanitized = sanitize_response_data(data)
        if isinstance(sanitized, dict):
            response.update(sanitized)
        else:
            response["data"] = sanitized

    if _meta is not None:
        response["_meta"] = _meta

    return json.dumps(response, indent=2, ensure_ascii=False)


def _error_response(error: str) -> str:
    """Format an error tool response. All tools MUST use this."""
    return json.dumps({"success": False, "error": str(error)}, indent=2, ensure_ascii=False)


def _error_dict_extended(
    code: str,
    message: str,
    retryable: bool,
    suggestion: str | None = None,
    available_names: list[str] | None = None,
) -> dict[str, Any]:
    """Return a structured error dict (extended L2+ error contract).

    For internal function composition before JSON serialization.

    Args:
        code: Machine-readable UPPER_SNAKE_CASE identifier (e.g. ``TIMEOUT``).
        message: Human-readable error message.
        retryable: Whether the agent SHOULD retry with backoff.
        suggestion: Optional one-sentence actionable next step.
        available_names: Optional list of valid alternatives (capped at 50).

    Returns:
        Dict of the form ``{"success": False, "error": {...}}``.
    """
    error: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if suggestion:
        error["suggestion"] = suggestion
    if available_names:
        error["available_names"] = available_names[:50]
    return {"success": False, "error": error}


def _error_response_extended(
    code: str,
    message: str,
    retryable: bool,
    suggestion: str | None = None,
    available_names: list[str] | None = None,
) -> str:
    """Format a structured error tool response (extended L2+ error contract).

    Args:
        code: Machine-readable UPPER_SNAKE_CASE identifier (e.g. ``TIMEOUT``).
        message: Human-readable error message.
        retryable: Whether the agent SHOULD retry with backoff.
        suggestion: Optional one-sentence actionable next step.
        available_names: Optional list of valid alternatives (capped at 50).

    Returns:
        JSON string with a structured ``error`` object.
    """
    return json.dumps(
        _error_dict_extended(code, message, retryable, suggestion, available_names),
        indent=2,
        ensure_ascii=False,
    )


# =============================================================================
# RESPONSE ENVELOPE — build_meta + sanitize_response_data
# =============================================================================


def build_meta(tool_name: str, start_time: float) -> dict[str, object]:
    """Build _meta envelope dict with request_id, duration_ms and tool_version.

    The request_id is read from the current observability context so it is
    the SAME id written to that invocation's log lines (Observability-10).

    Args:
        tool_name: Name of the tool being invoked.
        start_time: Time.monotonic() value captured at tool entry.

    Returns:
        Dict with request_id, duration_ms and tool_version for _meta envelope.
    """
    from tools import TOOLS_VERSION as _tools_ver
    from tools.observability import get_request_id

    return {
        "request_id": get_request_id(),
        "duration_ms": int((time.monotonic() - start_time) * 1000),
        "tool_version": _tools_ver,
    }


def sanitize_response_data(data: object) -> object:
    """Recursively sanitize response data before returning to the agent.

    Applies sanitize_log_line() to every string value in the structure.
    This is a SEPARATE trust boundary from log sanitization — a credential
    read from a backend would reach the agent even if logging is clean.
    """
    if isinstance(data, str):
        return sanitize_log_line(data)
    if isinstance(data, dict):
        return {k: sanitize_response_data(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_response_data(item) for item in data]
    return data


# Backward-compatible aliases
success_response = _success_response
error_response = _error_response

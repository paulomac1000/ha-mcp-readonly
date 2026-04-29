"""
Shared Utilities for MCP Tools

Provides common functionality used across all tools:
- HTTP client for Home Assistant API with retry logic
- Registry file loading with caching and blocklist
- Log sanitization (security — prevents token/credential leaks)
- Common helper functions

CHANGES vs original:
- Added BLOCKED_REGISTRIES frozenset (security)
- Added sanitize_log_line() (security)
- Added get_registry_cache_stats() (observability)
- Enhanced load_registry() with blocklist + hit/miss counters
- Added invalidate_registry_cache() for manual invalidation
- All existing public functions preserved — zero breaking changes
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# =============================================================================
# CONFIGURATION
# =============================================================================

_REGISTRY_CACHE: Dict[str, Tuple[Any, float]] = {}
_REGISTRY_TTL = 300  # seconds — 5 minutes

_REGISTRY_CACHE_STATS: Dict[str, int] = {"hits": 0, "misses": 0, "blocked": 0}

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

_SENSITIVE_PATTERNS: List[Tuple[re.Pattern, str]] = [
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

    This function is designed to be called at the output boundary — just
    before log content is serialised into a tool response.
    """
    for pattern, replacement in _SENSITIVE_PATTERNS:
        line = pattern.sub(replacement, line)
    return line


def get_registry_cache_stats() -> Dict[str, Any]:
    """
    Return cache hit / miss / blocked statistics for monitoring.

    Useful for verifying the 70%+ hit rate target in production.
    """
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
    data: Optional[Dict] = None,
    timeout: int = 10,
    retries: int = 3,
    backoff: float = 1.0,
) -> Dict[str, Any]:
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

    last_error: Optional[str] = None

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
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))

    return {"success": False, "error": last_error}


# =============================================================================
# REGISTRY LOADING WITH CACHE + BLOCKLIST
# =============================================================================


def load_registry(
    registry_name: str,
    config_path: str,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Load a registry file from ``.storage`` with caching and blocklist.

    Blocked registries (``auth``, ``auth_provider.homeassistatet``,
    ``onboarding``) silently return ``{}`` to prevent credential leaks.
    """
    if registry_name in BLOCKED_REGISTRIES:
        _REGISTRY_CACHE_STATS["blocked"] += 1
        return {}

    cache_key = f"{config_path}/{registry_name}"
    now = time.time()

    if use_cache and cache_key in _REGISTRY_CACHE:
        data, timestamp = _REGISTRY_CACHE[cache_key]
        if now - timestamp < _REGISTRY_TTL:
            _REGISTRY_CACHE_STATS["hits"] += 1
            return data

    _REGISTRY_CACHE_STATS["misses"] += 1

    try:
        path = Path(config_path) / ".storage" / registry_name
        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        _REGISTRY_CACHE[cache_key] = (data, now)
        return data

    except (json.JSONDecodeError, IOError, KeyError) as exc:
        print(f"[utils] Error loading registry {registry_name}: {exc}")
        _REGISTRY_CACHE.pop(cache_key, None)
        return {}


def invalidate_registry_cache(
    registry_name: Optional[str] = None,
    config_path: Optional[str] = None,
) -> None:
    """
    Invalidate registry cache entries.

    Call with no arguments to flush all entries.
    """
    global _REGISTRY_CACHE

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


def get_registry_entities(config_path: str) -> List[Dict]:
    """Shorthand: get entities from entity registry."""
    return load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])


def get_registry_devices(config_path: str) -> List[Dict]:
    """Shorthand: get devices from device registry."""
    return load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])


def get_registry_areas(config_path: str) -> List[Dict]:
    """Shorthand: get areas from area registry."""
    return load_registry("core.area_registry", config_path).get("data", {}).get("areas", [])


def get_registry_config_entries(config_path: str) -> List[Dict]:
    """Shorthand: get config entries from registry."""
    return load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])


# =============================================================================
# LOG FILE UTILITIES
# =============================================================================


def tail_log_file(
    log_path: str,
    lines: int = 1000,
    encoding: str = "utf-8",
) -> List[str]:
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
        print(f"[utils] Error reading log file: {exc}")
        return []


# =============================================================================
# COMMON HELPERS
# =============================================================================


def get_best_name(item: Dict, item_type: str = "entity") -> str:
    """
    Best available name for an entity or device.

    Priority: ``name_by_user > name > original_name > entity_id/id``.
    """
    if item_type == "device":
        return item.get("name_by_user") or item.get("name") or "Unknown Device"
    return item.get("name") or item.get("original_name") or item.get("entity_id", "Unknown")


def resolve_area_id(entity: Dict, device_map: Dict[str, Dict]) -> Optional[str]:
    """Resolve area: entity area → device area → ``None``."""
    if entity.get("area_id"):
        return entity["area_id"]
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

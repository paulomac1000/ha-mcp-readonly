"""Final response redaction for all model-visible operation results."""

from __future__ import annotations

import re
from typing import Any

from tools.utils import sanitize_log_line

REDACTED = "[REDACTED]"
_NORMALIZE_KEY = re.compile(r"[^a-z0-9]+")
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "api_token",
        "secret",
        "client_secret",
        "private_key",
        "authorization",
        "cookie",
        "set_cookie",
        "credential",
        "credentials",
        "ssid",
    }
)
_SENSITIVE_SUFFIXES = (
    "_password",
    "_passwd",
    "_token",
    "_secret",
    "_api_key",
    "_private_key",
    "_credential",
    "_credentials",
    "_cookie",
)


def _normalized_key(value: object) -> str:
    return _NORMALIZE_KEY.sub("_", str(value).strip().casefold()).strip("_")


def _is_sensitive_key(value: object) -> bool:
    key = _normalized_key(value)
    return key in _SENSITIVE_KEYS or any(key.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def sanitize_response_data(value: Any) -> Any:
    """Recursively remove credential-bearing fields and sanitize free-form strings.

    Key-aware redaction runs before string-pattern sanitization. This prevents a
    secret stored as a plain value under a field such as ``password`` or
    ``refresh_token`` from bypassing regexes that only see the value itself.
    """
    if isinstance(value, str):
        return sanitize_log_line(value)
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive_key(key) else sanitize_response_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_response_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_response_data(item) for item in value)
    return value

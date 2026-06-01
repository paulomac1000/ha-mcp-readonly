"""Tool manifests — Single Source of Truth for tool capability metadata.

Provides:
- TOOL_MANIFESTS dict (name -> capability profile)
- Factory functions for READ, WRITE, DESTRUCTIVE manifests
- _inject_risk_prefixes() for dynamic risk annotation injection
"""

from typing import Any

from tools import TOOLS_VERSION

_TOOL_MANIFESTS: dict[str, dict[str, Any]] = {}

KNOWN_RISK_PREFIXES = frozenset(
    {"[READ]", "[WRITE]", "[DANGEROUS]", "[DESTRUCTIVE]", "[SENSITIVE]"}
)


def register_manifest(name: str, manifest: dict[str, Any]) -> None:
    """Register a tool manifest."""
    manifest.setdefault("name", name)
    manifest.setdefault("version", TOOLS_VERSION)
    _TOOL_MANIFESTS[name] = manifest


def get_manifest(name: str) -> dict[str, Any] | None:
    """Get manifest for a tool by name."""
    return _TOOL_MANIFESTS.get(name)


def get_all_manifests() -> dict[str, dict[str, Any]]:
    """Return all registered manifests."""
    return dict(_TOOL_MANIFESTS)


def make_manifest(
    name: str, timeout_ms: int = 15000, latency: str = "moderate", cost: str = "cheap"
) -> dict[str, Any]:
    """Factory for READ tool manifests."""
    return {
        "name": name,
        "version": TOOLS_VERSION,
        "risk": "READ",
        "side_effects": "read",
        "idempotent": True,
        "retryable": True,
        "concurrent_safe": True,
        "timeout_ms": timeout_ms,
        "requires_confirmation": False,
        "determinism": "env-dependent",
        "latency": latency,
        "cost": cost,
        "impact": "none",
        "privacy": "none",
        "reversible": True,
    }


def _make_write_manifest(
    name: str, timeout_ms: int = 15000, latency: str = "moderate"
) -> dict[str, Any]:
    """Factory for WRITE tool manifests."""
    return {
        "name": name,
        "version": TOOLS_VERSION,
        "risk": "WRITE",
        "side_effects": "write",
        "idempotent": True,
        "retryable": True,
        "concurrent_safe": False,
        "timeout_ms": timeout_ms,
        "requires_confirmation": True,
        "determinism": "env-dependent",
        "latency": latency,
        "cost": "moderate",
        "impact": "persistent",
        "privacy": "none",
        "reversible": True,
    }


def _make_destructive_manifest(
    name: str, timeout_ms: int = 30000, latency: str = "slow"
) -> dict[str, Any]:
    """Factory for DESTRUCTIVE tool manifests (reboot, reset, delete)."""
    return {
        "name": name,
        "version": TOOLS_VERSION,
        "risk": "DESTRUCTIVE",
        "side_effects": "destructive",
        "idempotent": False,
        "retryable": False,
        "concurrent_safe": False,
        "timeout_ms": timeout_ms,
        "requires_confirmation": True,
        "determinism": "env-dependent",
        "latency": latency,
        "cost": "expensive",
        "impact": "service_outage",
        "privacy": "none",
        "reversible": False,
    }


def auto_register_all_read_tools(tool_names: set[str]) -> None:
    """Register a default READ manifest for every tool not already in TOOL_MANIFESTS."""
    for name in sorted(tool_names):
        if name not in _TOOL_MANIFESTS:
            register_manifest(name, make_manifest(name))


def _inject_risk_prefixes(registered_tools: dict[str, Any]) -> None:
    """Dynamically inject risk prefix into tool docstrings from manifests.

    Strips any existing risk prefix, then prepends the correct one
    from TOOL_MANIFESTS.  Also updates the tool's ``description``
    attribute so downstream introspection sees the prefix.
    """
    for name, tool in registered_tools.items():
        manifest = _TOOL_MANIFESTS.get(name, {})
        risk = manifest.get("risk", "READ")

        raw_fn = tool
        for attr in ("fn", "func", "_func", "function"):
            if hasattr(tool, attr):
                inner = getattr(tool, attr)
                if callable(inner):
                    raw_fn = inner
                    break

        doc = (raw_fn.__doc__ or "").strip()
        for prefix in KNOWN_RISK_PREFIXES:
            if doc.startswith(prefix):
                doc = doc[len(prefix) :].lstrip()
                break

        new_doc = f"[{risk}] {doc}"
        raw_fn.__doc__ = new_doc

        if hasattr(tool, "description"):
            tool.description = new_doc.split("\n")[0].rstrip(".")


def _make_meta_wrapper(raw_fn: Any, tool_name: str) -> Any:
    """Build a wrapper that injects a ``_meta`` envelope into a tool response.

    The wrapper assigns a request_id, counts the invocation, measures
    duration, and merges ``{request_id, duration_ms, tool_version}`` into the
    tool's JSON response. It deliberately does NOT catch exceptions: every
    tool already owns a tested ``try/except`` (the two-layer pattern), and
    argument-binding ``TypeError``s must still surface to the transport so
    the REST bridge can answer ``400``.
    """
    import functools
    import inspect
    import json
    import time

    from tools.observability import increment_invocation, start_tool_context
    from tools.utils import build_meta

    def _augment(result: Any, start: float) -> Any:
        if not isinstance(result, str):
            return result
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return result
        if not isinstance(parsed, dict):
            return result
        parsed["_meta"] = build_meta(tool_name, start)
        return json.dumps(parsed, indent=2, ensure_ascii=False)

    if inspect.iscoroutinefunction(raw_fn):

        @functools.wraps(raw_fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            start_tool_context()
            increment_invocation(tool_name)
            result = await raw_fn(*args, **kwargs)
            return _augment(result, start)

        setattr(async_wrapper, "_meta_wrapped", True)
        return async_wrapper

    @functools.wraps(raw_fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        start_tool_context()
        increment_invocation(tool_name)
        result = raw_fn(*args, **kwargs)
        return _augment(result, start)

    setattr(sync_wrapper, "_meta_wrapped", True)
    return sync_wrapper


def _inject_meta_envelope(registered_tools: dict[str, Any]) -> None:
    """Wrap every registered tool so each response carries a ``_meta`` envelope.

    Single central injection point: individual tools never build ``_meta``
    themselves. Run this AFTER ``_inject_risk_prefixes`` so the wrapper
    inherits the prefixed docstring.
    """
    for name, tool in registered_tools.items():
        for attr in ("fn", "func", "_func", "function"):
            if hasattr(tool, attr):
                inner = getattr(tool, attr)
                if callable(inner) and not getattr(inner, "_meta_wrapped", False):
                    setattr(tool, attr, _make_meta_wrapper(inner, name))
                    break

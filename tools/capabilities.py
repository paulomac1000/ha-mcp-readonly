"""Capability introspection tool.

Exposes the full tool catalog with capability manifests over the MCP
transport itself. The REST endpoint ``GET /api/tools/{name}/manifest`` is
unreachable for an agent connected over pure MCP/SSE; this tool closes that
gap (mcp-server-standards.md, rule 2b, L3+).
"""

import logging
from typing import Any

from tools import TOOLS_VERSION
from tools.manifests import get_all_manifests, make_manifest, register_manifest
from tools.utils import _error_response, _success_response

_logger = logging.getLogger(__name__)

CAPABILITIES_SCHEMA_VERSION = "1.0"


def _do_describe_ha_capabilities() -> dict[str, Any]:
    """Build the capability catalog from registered tool manifests. Zero I/O.

    Returns:
        Dict with schema_version, server name, tools_version, supported
        transports, tool_count and the sorted list of tool manifests.
    """
    manifests = get_all_manifests()
    tools = sorted(manifests.values(), key=lambda m: str(m.get("name", "")))
    return {
        "schema_version": CAPABILITIES_SCHEMA_VERSION,
        "server": "HA-Observer",
        "tools_version": TOOLS_VERSION,
        "transports": ["sse", "rest"],
        "tool_count": len(tools),
        "tools": tools,
    }


def register_capability_tools(mcp: Any) -> None:
    """Register the capability introspection tool on the MCP server."""

    register_manifest(
        "describe_ha_capabilities",
        make_manifest("describe_ha_capabilities", timeout_ms=1000, latency="instant"),
    )

    @mcp.tool()
    async def describe_ha_capabilities() -> str:
        """Return the catalog of registered tools with their capability manifests.

        This is a zero-I/O introspection tool. It lets an AI agent inspect
        every tool's risk level, side effects, determinism, latency and other
        manifest metadata without invoking the tools themselves. Unlike the
        REST-only manifest endpoint, this works over the MCP/SSE transport.

        Args:
            None.

        Returns:
            JSON string with a ``success`` flag and a payload containing
            ``schema_version``, ``tools_version``, supported ``transports``,
            ``tool_count`` and the list of tool manifests.
        """
        try:
            return _success_response(_do_describe_ha_capabilities())
        except Exception as exc:
            _logger.error("describe_ha_capabilities failed: %s", exc)
            return _error_response(str(exc))

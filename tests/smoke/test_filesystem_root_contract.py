"""Live smoke contract for configurable filesystem-root discovery."""

import pytest
import requests

from .conftest import HA_TOKEN, REST_API_URL, REST_AUTH_CONFIGURED, REST_HEADERS, _server_running

pytestmark = pytest.mark.skipif(
    not _server_running()
    or not HA_TOKEN
    or not REST_AUTH_CONFIGURED
    or HA_TOKEN in ("", "your_long_lived_access_token_here"),
    reason="MCP server not running, or HA_TOKEN or REST API token not configured",
)


def _call_tool(tool_name: str, **params: object) -> dict:
    response = requests.post(
        f"{REST_API_URL}/api/tools/{tool_name}",
        json=params,
        headers=REST_HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    assert isinstance(payload, dict), f"{tool_name} did not return a JSON object"
    return payload


def test_default_list_directory_reports_the_configured_root() -> None:
    """Omitted path must expose the real allowlisted root without a /config fallback."""
    discovered = _call_tool("list_directory")
    assert discovered.get("success") is True
    result = discovered.get("result")
    assert isinstance(result, dict), "list_directory result must be an object"
    root = result.get("path")
    assert isinstance(root, str) and root.strip(), "configured root was not reported"

    explicit = _call_tool("list_directory", path=root)
    assert explicit.get("success") is True
    explicit_result = explicit.get("result")
    assert isinstance(explicit_result, dict)
    assert explicit_result.get("path") == root

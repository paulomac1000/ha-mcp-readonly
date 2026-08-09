"""Smoke test: verify ALL callable tools return standard JSON with success field."""

import pytest
import requests

from .conftest import HA_TOKEN, REST_API_URL, REST_HEADERS, _server_running

pytestmark = pytest.mark.skipif(
    not _server_running()
    or not HA_TOKEN
    or not REST_HEADERS["Authorization"].startswith("Bearer ")
    or HA_TOKEN in ("", "your_long_lived_access_token_here"),
    reason="MCP server not running or HA_TOKEN not configured",
)


def _list_tools():
    resp = requests.get(f"{REST_API_URL}/api/tools", headers=REST_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()["tools"]


# Tools known to be slow or environment-dependent on a real HA instance.
# They are exercised by dedicated tests; the envelope check skips them so a
# single 504 in one heavy tool does not mask envelope regressions elsewhere.
_KNOWN_ENV_FAIL: set[str] = set()


def _call_tool_safe(tool_name, **params):
    """Call a tool, return (data, status_code) or (None, 0) on transport error."""
    try:
        resp = requests.post(
            f"{REST_API_URL}/api/tools/{tool_name}",
            json=params or {},
            headers=REST_HEADERS,
            timeout=120,
        )
        try:
            return resp.json(), resp.status_code
        except ValueError:
            return None, resp.status_code
    except requests.RequestException:
        return None, 0


class TestResponseFormatCompliance:
    """Every zero-parameter tool must return a standard JSON with success field."""

    def test_all_tools_return_success_field(self):
        """All zero-param-callable tools should have success field in response."""
        tools = _list_tools()
        missing = []
        errors = []

        for tool in tools:
            name = tool["name"]
            if name in _KNOWN_ENV_FAIL:
                continue

            data, status = _call_tool_safe(name)
            if status == 400 and data.get("error", {}).get("code") == "INVALID_ARGUMENTS":
                # Tool requires parameters — out of scope for the zero-param envelope check.
                continue
            if data is None:
                errors.append(f"{name}: HTTP {status or 'timeout'}")
                continue

            if "success" not in data:
                result = data.get("result", {})
                if isinstance(result, dict):
                    if "success" not in result:
                        missing.append(name)
                else:
                    missing.append(name)

        assert len(missing) == 0, f"Tools missing success field: {missing}\nErrors: {errors}"
        assert len(errors) == 0, f"Tools with errors: {errors}"

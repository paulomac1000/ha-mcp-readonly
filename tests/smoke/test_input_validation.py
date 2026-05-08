"""Smoke tests: verify tools don't crash on invalid inputs."""

import requests

from .conftest import REST_API_URL


def _call_tool(tool_name, **params):
    resp = requests.post(
        f"{REST_API_URL}/api/tools/{tool_name}",
        json=params,
        timeout=30,
    )
    return resp


class TestInputValidation:
    """Critical tools must handle None/empty inputs gracefully."""

    def test_get_automation_code_none(self):
        """None automation_id should not crash (500)."""
        resp = _call_tool("get_automation_code", automation_id=None)
        assert resp.status_code in (200, 400)
        data = resp.json()
        result = data.get("result", {})
        assert result.get("success") is False or data.get("success") is False

    def test_get_automation_code_empty(self):
        """Empty string automation_id should not crash."""
        resp = _call_tool("get_automation_code", automation_id="")
        assert resp.status_code in (200, 400)
        data = resp.json()
        result = data.get("result", {})
        assert result.get("success") is False or data.get("success") is False

    def test_get_entity_state_empty(self):
        """Empty entity_id should return error, not 500."""
        resp = _call_tool("get_entity_state", entity_id="")
        assert resp.status_code in (200, 400)
        data = resp.json()
        result = data.get("result", {})
        assert result.get("success") is False

    def test_search_entities_empty(self):
        """Empty search_term should not crash the server."""
        resp = _call_tool("search_entities", search_term="")
        assert resp.status_code in (200, 400)

    def test_get_entity_context_empty(self):
        """Empty entity_id for context should not crash."""
        resp = _call_tool("get_entity_context", entity_id="")
        assert resp.status_code in (200, 400)

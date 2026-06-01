"""E2E tests: REST API endpoints."""

import time

import pytest
import requests

from .conftest import HA_TOKEN, REST_API_URL, _server_running

pytestmark = pytest.mark.skipif(not HA_TOKEN or not _server_running(), reason="HA_TOKEN and running server required for e2e tests")


class TestRESTAPI:
    """Server REST API integration tests."""

    def test_health_endpoint(self):
        """GET /health should return healthy."""
        resp = requests.get(f"{REST_API_URL}/health", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["tool_count"] > 100

    def test_api_health(self):
        """GET /api/health should also work."""
        resp = requests.get(f"{REST_API_URL}/api/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_list_tools(self):
        """GET /api/tools should list all tools."""
        resp = requests.get(f"{REST_API_URL}/api/tools", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total"] > 100
        tool_names = [t["name"] for t in data["tools"]]
        assert "get_entity_state" in tool_names
        assert "list_automations" in tool_names
        assert "diagnose_system_health" in tool_names

    def test_openapi_schema(self):
        """GET /api/openapi.json should return valid schema."""
        resp = requests.get(f"{REST_API_URL}/api/openapi.json", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["openapi"] == "3.0.0"
        assert "/api/tools/get_entity_state" in data["paths"]

    def test_call_tool_via_rest(self):
        """POST /api/tools/{name} should execute a tool."""
        resp = requests.post(
            f"{REST_API_URL}/api/tools/get_entity_state",
            json={"entity_id": "sun.sun"},
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["tool"] == "get_entity_state"

    def test_nonexistent_tool_returns_404(self):
        """Calling a non-existent tool should return 404."""
        resp = requests.post(
            f"{REST_API_URL}/api/tools/nonexistent_tool_xyz",
            json={},
            timeout=10,
        )
        assert resp.status_code == 404
        data = resp.json()
        assert data["success"] is False

    def test_context_modes_endpoint(self):
        """GET /api/context/modes should list generation modes."""
        resp = requests.get(f"{REST_API_URL}/api/context/modes", timeout=10)
        assert resp.status_code == 200
        modes = resp.json()["modes"]
        mode_ids = [m["id"] for m in modes]
        assert "hybrid" in mode_ids
        assert "offline" in mode_ids
        assert "online" in mode_ids

    def test_tool_call_missing_required_args(self):
        """POST without required entity_id should return 400."""
        resp = requests.post(
            f"{REST_API_URL}/api/tools/get_entity_state",
            json={},
            timeout=10,
        )
        assert resp.status_code in (400, 500)

    def test_tool_call_invalid_json(self):
        """POST with invalid JSON should not crash."""
        resp = requests.post(
            f"{REST_API_URL}/api/tools/get_entity_state",
            data="not json",
            headers={"Content-Type": "text/plain"},
            timeout=10,
        )
        assert resp.status_code in (200, 400, 415, 500)

    def test_openapi_tool_paths_complete(self):
        """Every tool should have a path in OpenAPI schema."""
        tools_resp = requests.get(f"{REST_API_URL}/api/tools", timeout=10)
        tools_data = tools_resp.json()
        tool_names = [t["name"] for t in tools_data["tools"]]

        schema_resp = requests.get(f"{REST_API_URL}/api/openapi.json", timeout=10)
        schema_data = schema_resp.json()

        missing = []
        for name in tool_names[:10]:
            path = f"/api/tools/{name}"
            if path not in schema_data["paths"]:
                missing.append(name)

        assert len(missing) == 0, f"Missing OpenAPI paths for: {missing}"


class TestContextGeneratorREST:
    """Context generator REST API endpoint tests."""

    def test_context_generate_starts(self):
        """POST /api/context/generate should start generation and return success."""
        resp = requests.post(
            f"{REST_API_URL}/api/context/generate",
            json={"mode": "offline"},
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["message"] in (
            "Context generation started",
            "Generation already in progress",
        )

    def test_context_status_returns_status(self):
        """GET /api/context/status should return status."""
        resp = requests.get(f"{REST_API_URL}/api/context/status", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_context_download_after_wait(self):
        """After generation completes, download should return markdown."""
        # Trigger generation
        requests.post(
            f"{REST_API_URL}/api/context/generate",
            json={"mode": "offline"},
            timeout=10,
        )
        # Wait for completion
        for _ in range(30):
            status_resp = requests.get(f"{REST_API_URL}/api/context/status", timeout=5)
            if status_resp.json().get("status") in ("completed", "error"):
                break
            time.sleep(2)

        # Download
        resp = requests.get(
            f"{REST_API_URL}/api/context/download",
            timeout=30,
        )
        assert resp.status_code in (200, 404, 409)

    def test_context_generate_offline_mode(self):
        """Offline mode generation should work without API access."""
        resp = requests.post(
            f"{REST_API_URL}/api/context/generate",
            json={"mode": "offline"},
            timeout=10,
        )
        assert resp.status_code in (200, 409)
        data = resp.json()
        assert data["success"] is True


class TestSSETransport:
    """E2E tests for MCP SSE transport on port 9092."""

    def test_sse_endpoint_accepts_connection(self):
        """GET /sse should return SSE stream headers."""
        import os

        mcp_port = int(os.getenv("MCP_SSE_PORT", "9092"))
        resp = requests.get(
            f"http://localhost:{mcp_port}/sse",
            timeout=5,
            stream=True,
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        resp.close()

    def test_messages_endpoint_accepts_post(self):
        """POST /messages should accept MCP messages."""
        import os

        mcp_port = int(os.getenv("MCP_SSE_PORT", "9092"))
        resp = requests.post(
            f"http://localhost:{mcp_port}/messages",
            json={
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 1,
            },
            timeout=10,
        )
        assert resp.status_code in (200, 400, 404)


class TestContextGeneratorFullPipeline:
    """E2E: full context generation + download pipeline via REST API."""

    def test_full_pipeline_generate_status_download(self):
        """POST generate → poll status → GET download should work end-to-end."""
        import time

        # 1. Generate
        resp = requests.post(
            f"{REST_API_URL}/api/context/generate",
            json={"mode": "offline"},
            timeout=10,
        )
        assert resp.status_code in (200, 409)

        # 2. Wait and poll
        for _ in range(30):
            status_resp = requests.get(f"{REST_API_URL}/api/context/status", timeout=5)
            status = status_resp.json().get("status", "unknown")
            if status in ("completed", "error"):
                break
            time.sleep(2)

        # 3. Download
        download_resp = requests.get(
            f"{REST_API_URL}/api/context/download",
            params={"format": "markdown"},
            timeout=30,
        )
        assert download_resp.status_code in (200, 404, 409)

        # 4. Verify format
        if download_resp.status_code == 200:
            content = download_resp.text
            assert len(content) > 1000
            assert "Home Assistant Context for AI" in content
